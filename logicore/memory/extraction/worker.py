"""
LLM-based memory extraction worker.

Extracts memories from conversations using an LLM as a background worker,
independent of the main chat LLM thread.

Improvements over the original:
- Pydantic-validated LLM outputs (no silent schema failures)
- Retry/backoff for transient LLM failures
- Manifest filtered to relevant domains (avoids context overflow)
- Extraction uses the new MemoryDomain/MemoryKind taxonomy consistently
- Configurable throttle interval and transcript window
- Persistent extraction queue (survives process restart)
- Improved update prompt with explicit target guidance
"""

import asyncio
import json
import time
import re
import logging
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from pathlib import Path
from enum import Enum

from pydantic import BaseModel, Field, ValidationError, field_validator

from logicore.memory.types import (
    MemoryMetadata, MemoryType, MemoryDomain, MemoryKind,
    MemoryStability, LEGACY_TYPE_MAPPING,
)
from logicore.memory.storage import MemoryStore
from logicore.providers.policies import RetryPolicy, FailureCategory
from logicore.providers.policies import _classify_failure_standalone

logger = logging.getLogger(__name__)

# Extraction prompts
EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction subagent. Your task is to analyze conversations and extract meaningful memories that should persist across sessions.

You have access to a memory store where you can write memory files. Each memory file is a markdown file with YAML frontmatter.

MEMORY DOMAINS (which aspect of the user/context):
- identity: who the user is (role, background, skills, experience)
- preferences: how the user likes to work (feedback, style, habits)
- relationships: people/teams the user interacts with
- knowledge: project goals, status, decisions, architecture
- domain: domain/business-specific knowledge
- additional_learning: insights, patterns, trends discovered

MEMORY KINDS (what kind of memory):
- fact: a concrete statement
- guideline: a rule or preference to follow
- context: background that frames future work
- pointer: a reference to an external system/location

MEMORY STABILITY:
- stable: Rarely changes (e.g., user's job role)
- evolving: Changes occasionally (e.g., project goals)
- volatile: Changes frequently (e.g., current task status)
- ephemeral: Very short-lived (e.g., temporary context)

GUIDELINES:
1. Only extract genuinely useful information that would help in future sessions
2. Do not duplicate existing memories - read the manifest first
3. Assign appropriate confidence (0-1) based on how certain you are
4. Assign importance (0-1) based on how critical for personalization
5. Use appropriate stability level
6. Include relevant tags for fuzzy matching
7. If nothing new to remember, return an empty list

OUTPUT FORMAT:
Return a JSON array of memory operations. Each operation is one of:
- {"action": "create", "filename": "name.md", "metadata": {...}, "body": "..."}
- {"action": "update", "filename": "existing.md", "metadata": {...}, "body": "..."}
- {"action": "delete", "filename": "stale.md"}

The metadata object MUST include: name, description, type, domain, kind, confidence, importance, stability, tags
- domain: one of identity|preferences|relationships|knowledge|domain|additional_learning
- kind: one of fact|guideline|context|pointer
- stability: one of stable|evolving|volatile|ephemeral
- type: one of user|feedback|project|reference (legacy hint; domain/kind take precedence)
"""

EXTRACTION_USER_PROMPT = """Analyze this conversation and extract memories that should persist across sessions.

EXISTING MEMORY MANIFEST (filtered to relevant domains):
{manifest}

CONVERSATION TRANSCRIPT:
{transcript}

Extract memories as a JSON array of operations. If nothing new to remember, return an empty array [].
"""

UPDATE_PROMPT = """Update the memory at {filename} with new information from the conversation.

EXISTING MEMORY (current content):
---BEGIN---
{existing_content}
---END---

NEW INFORMATION FROM CONVERSATION:
---BEGIN---
{new_info}
---END---

INSTRUCTIONS:
- Preserve everything that is still true in the existing memory.
- Merge the new information in, resolving any contradictions (prefer newer info).
- Do NOT invent details not present in either source.
- Return ONLY the updated memory body (markdown, no frontmatter).
"""


# ---------------------------------------------------------------------------
# Pydantic schemas for validated LLM output
# ---------------------------------------------------------------------------

class _OpMetadata(BaseModel):
    name: Optional[str] = None
    description: str = ""
    type: str = "project"
    domain: Optional[str] = None
    kind: Optional[str] = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    stability: str = "evolving"
    tags: List[str] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, v):
        if v is not None and v not in {d.value for d in MemoryDomain}:
            return None
        return v

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v):
        if v is not None and v not in {k.value for k in MemoryKind}:
            return None
        return v

    @field_validator("stability")
    @classmethod
    def _valid_stability(cls, v):
        if v not in {s.value for s in MemoryStability}:
            return "evolving"
        return v

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v):
        if v not in {t.value for t in MemoryType}:
            return "project"
        return v


class _Operation(BaseModel):
    action: str
    filename: str
    metadata: Optional[_OpMetadata] = None
    body: str = ""


def _parse_operations_validated(response: str) -> List[_Operation]:
    """Parse + validate LLM response into operation objects."""
    match = re.search(r"\[.*\]", response, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if isinstance(raw, dict):
        for key in ("memories", "operations", "results", "items", "extracted"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return []

    ops: List[_Operation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        filename = item.get("filename")
        if action not in ("create", "update", "delete") or not filename:
            continue
        try:
            ops.append(_Operation.model_validate(item))
        except ValidationError as e:
            logger.debug(f"[ExtractionWorker] Skipping invalid operation: {e}")
    return ops


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class ExtractionWorker:
    """
    Background worker for LLM-based memory extraction.

    Runs independently of the main chat thread, using a separate LLM
    instance to extract memories from conversations.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        llm_provider: Any,
        debug: bool = False,
        on_extract: Optional[Callable[[List[MemoryMetadata]], None]] = None,
        throttle_interval: float = 1.0,
        transcript_window: int = 20,
        manifest_limit: int = 40,
        persistence_path: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        """
        Args:
            memory_store: Store to write extracted memories
            llm_provider: LLM provider for extraction (instance or resolved name)
            debug: Enable debug logging
            on_extract: Callback after successful extraction
            throttle_interval: Seconds between extractions (configurable)
            transcript_window: Max recent messages sent to the LLM
            manifest_limit: Max manifest entries sent to the LLM
            persistence_path: Where to persist the pending queue (JSONL)
            retry_policy: Retry policy for LLM calls
        """
        self.memory_store = memory_store
        self.llm_provider = llm_provider
        self.debug = debug
        self.on_extract = on_extract
        self._running = False
        self._extraction_queue: asyncio.Queue = asyncio.Queue()
        self._last_cursor: Optional[str] = None
        self._extraction_count = 0
        self._throttle_interval = throttle_interval
        self._transcript_window = transcript_window
        self._manifest_limit = manifest_limit
        self._persistence_path = persistence_path
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=3)
        self._max_retries = 3
        self._failed_items: List[Dict[str, Any]] = []

    async def start(self) -> None:
        """Start the extraction worker."""
        if self._running:
            return

        # Restore any persisted queue
        self._restore_queue()

        self._running = True
        asyncio.create_task(self._process_queue())
        if self.debug:
            logger.debug("[ExtractionWorker] Started")

    async def stop(self) -> None:
        """Stop the extraction worker (persisting remaining queue)."""
        self._running = False
        # Drain remaining items to disk so they survive restart
        self._persist_queue()
        if self.debug:
            logger.debug("[ExtractionWorker] Stopped")

    async def submit_conversation(
        self,
        conversation: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> None:
        """Submit a conversation for memory extraction."""
        item = {
            "conversation": conversation,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
        }
        await self._extraction_queue.put(item)
        self._persist_queue()

    # -- persistence --------------------------------------------------------

    def _persist_queue(self) -> None:
        if not self._persistence_path:
            return
        try:
            path = Path(self._persistence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            items = list(self._extraction_queue._queue)  # type: ignore[attr-defined]
            items.extend(self._failed_items)
            with open(path, "w", encoding="utf-8") as f:
                for it in items:
                    f.write(json.dumps(it) + "\n")
        except Exception as e:
            if self.debug:
                logger.debug(f"[ExtractionWorker] Persist failed: {e}")

    def _restore_queue(self) -> None:
        if not self._persistence_path or not Path(self._persistence_path).exists():
            return
        try:
            with open(self._persistence_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    self._extraction_queue.put_nowait(json.loads(line))
            Path(self._persistence_path).unlink(missing_ok=True)
            if self.debug:
                logger.debug("[ExtractionWorker] Restored persisted queue")
        except Exception as e:
            if self.debug:
                logger.debug(f"[ExtractionWorker] Restore failed: {e}")

    # -- processing ---------------------------------------------------------

    async def _process_queue(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(
                    self._extraction_queue.get(),
                    timeout=1.0,
                )
                success = await self._extract_memories(item)
                if not success:
                    attempts = item.get("_attempts", 0) + 1
                    if attempts <= self._max_retries:
                        # Re-enqueue for another attempt (transient failure)
                        item["_attempts"] = attempts
                        await self._extraction_queue.put(item)
                        if self.debug:
                            logger.debug(
                                f"[ExtractionWorker] Re-queuing failed item "
                                f"(attempt {attempts}/{self._max_retries})"
                            )
                    else:
                        # Give up after max retries; keep for post-mortem
                        self._failed_items.append(item)
                self._extraction_queue.task_done()
                self._persist_queue()

                await asyncio.sleep(self._throttle_interval)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if self.debug:
                    logger.debug(f"[ExtractionWorker] Queue processing error: {e}")

    async def _extract_memories(self, item: Dict[str, Any]) -> bool:
        """Extract memories from a conversation. Returns True on success."""
        conversation = item["conversation"]
        session_id = item["session_id"]

        if not conversation:
            return True

        manifest = self._build_manifest(conversation)
        transcript = self._format_transcript(conversation)

        user_prompt = EXTRACTION_USER_PROMPT.format(
            manifest=manifest,
            transcript=transcript,
        )

        try:
            response = await self._call_llm_with_retry([
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
        except Exception as e:
            if self.debug:
                logger.debug(f"[ExtractionWorker] LLM call failed: {e}")
            return False

        if not response:
            # Empty response from the model: treat as a transient failure so
            # the item is retried (and eventually persisted) rather than
            # silently dropping the memory.
            if self.debug:
                logger.debug(
                    "[ExtractionWorker] Empty LLM response; marking for retry"
                )
            return False

        operations = self._parse_operations(response)
        if not operations:
            # Could be a genuine "nothing to remember" (explicit "[]") or a
            # malformed response. Distinguish: if the model returned a valid
            # empty JSON array, accept it; otherwise retry.
            if self._is_explicit_empty(response):
                return True
            if self.debug:
                logger.debug(
                    "[ExtractionWorker] Unparseable LLM response; marking for retry"
                )
                logger.debug(
                    f"[ExtractionWorker] Raw response (first 500 chars): {response}"
                )
            return False

        extracted: List[MemoryMetadata] = []
        for op in operations:
            result = await self._execute_operation(op)
            if result:
                extracted.append(result)

        if extracted:
            self.memory_store.update_index()
            self._extraction_count += 1
            if self.on_extract:
                self.on_extract(extracted)
            if self.debug:
                logger.debug(
                    f"[ExtractionWorker] Extracted {len(extracted)} memories "
                    f"from session {session_id}"
                )
        return True

    @staticmethod
    def _is_explicit_empty(response: str) -> bool:
        """True if the model explicitly returned an empty array (genuine
        'nothing to remember') versus malformed/no JSON output."""
        # Strip code fences (```json ... ```) before checking
        import re as _re
        fence_match = _re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, _re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else response.strip()
        if text in ("[]", ""):
            return True
        try:
            import json as _json
            data = _json.loads(text)
            return isinstance(data, list) and len(data) == 0
        except (json.JSONDecodeError, ValueError):
            return False

    def _build_manifest(self, conversation: Optional[List[Dict[str, Any]]] = None) -> str:
        """
        Build a manifest of existing memories, filtered to relevant domains
        inferred from the conversation so the LLM context stays small.
        """
        headers = self.memory_store.scan_memory_files()

        if not headers:
            return "No existing memories."

        # Infer relevant domains from the conversation text
        relevant = self._infer_domains(conversation or [])
        if relevant:
            filtered = [h for h in headers if (h.domain in relevant)]
            # Always keep some context even if nothing matched
            if len(filtered) < 5:
                filtered = headers
        else:
            filtered = headers

        filtered = filtered[: self._manifest_limit]

        lines = []
        for h in filtered:
            domain = h.domain.value if h.domain else "unknown"
            kind = h.kind.value if h.kind else "unknown"
            stability = h.stability.value if h.stability else "unknown"
            tags = ", ".join(h.tags) if h.tags else "none"
            lines.append(
                f"- {h.filename} [{domain}/{kind}] "
                f"(importance={h.importance}, stability={stability}): "
                f"{h.description} [tags: {tags}]"
            )

        note = "" if filtered == headers else (
            f"\n(showing {len(filtered)} of {len(headers)} memories; "
            "others omitted for brevity)"
        )
        return "\n".join(lines) + note

    def _infer_domains(
        self, conversation: List[Dict[str, Any]]
    ) -> List[MemoryDomain]:
        """Lightweight keyword-based domain inference for manifest filtering."""
        from logicore.memory.types import DOMAIN_KEYWORDS
        text = " ".join(
            str(m.get("content", "")) for m in conversation if isinstance(m, dict)
        ).lower()
        scores: Dict[MemoryDomain, int] = {}
        for domain, kws in DOMAIN_KEYWORDS.items():
            s = sum(1 for kw in kws if kw in text)
            if s:
                scores[domain] = s
        if not scores:
            return []
        # Keep domains that score at least half of the top score
        top = max(scores.values())
        return [d for d, s in scores.items() if s >= max(1, top // 2)]

    def _format_transcript(self, conversation: List[Dict[str, Any]]) -> str:
        lines = []
        for msg in conversation[-self._transcript_window:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                lines.append(f"{role}: {content[:500]}")
        return "\n".join(lines)

    # -- JSON extraction (kept for backward compatibility / tests) ----------

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract a JSON array/object from text, handling code fences."""
        code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1)
        array_match = re.search(r'\[.*\]', text, re.DOTALL)
        if array_match:
            return array_match.group(0)
        obj_match = re.search(r'\{.*\}', text, re.DOTALL)
        if obj_match:
            return obj_match.group(0)
        return None

    def _parse_operations(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse LLM response into a list of operation dicts.

        Invalid or malformed operations are dropped (no silent schema
        failures). Returns plain dicts for backward compatibility; internal
        execution additionally validates each op via pydantic.
        """
        if not response:
            return []
        json_str = self._extract_json(response)
        if not json_str:
            return []
        try:
            raw = json.loads(json_str)
        except json.JSONDecodeError:
            return []
        # Unwrap dict responses — models often return {"memories": [...]}
        # or {"operations": [...]} instead of a bare array.
        if isinstance(raw, dict):
            for key in ("memories", "operations", "results", "items", "extracted"):
                if isinstance(raw.get(key), list):
                    raw = raw[key]
                    break
        if not isinstance(raw, list):
            return []
        ops: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if item.get("action") not in ("create", "update", "delete"):
                continue
            ops.append(item)
        return ops

    async def _call_llm_with_retry(
        self, messages: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Call the LLM with retry/backoff for transient failures."""
        attempt = 1
        while True:
            try:
                response = await self.llm_provider.chat(messages)
                if hasattr(response, "content"):
                    return response.content
                return str(response)
            except Exception as e:
                category = _classify_failure_standalone(e)
                if not self._retry_policy.should_retry(category, attempt):
                    if self.debug:
                        logger.debug(
                            f"[ExtractionWorker] LLM call terminal failure "
                            f"({category}): {e}"
                        )
                    return None
                delay = self._retry_policy.calculate_delay(attempt, category)
                if self.debug:
                    logger.debug(
                        f"[ExtractionWorker] LLM call retry {attempt} "
                        f"after {delay:.1f}s ({category}): {e}"
                    )
                await asyncio.sleep(delay)
                attempt += 1

    async def _execute_operation(
        self, op: Dict[str, Any]
    ) -> Optional[MemoryMetadata]:
        """
        Execute a single extraction operation (dict form).

        The dict is validated through the pydantic schema; malformed
        operations are rejected (returning None) instead of silently
        producing bad memory files.
        """
        action = op.get("action")
        filename = op.get("filename")
        if action not in ("create", "update", "delete") or not filename:
            return None
        if not filename.endswith(".md"):
            filename += ".md"

        # Validate with pydantic (safe fallback for missing/invalid fields)
        try:
            validated = _Operation.model_validate(op)
        except ValidationError as e:
            if self.debug:
                logger.debug(f"[ExtractionWorker] Invalid operation dropped: {e}")
            return None

        try:
            if action == "create":
                return await self._create_memory(filename, validated)
            elif action == "update":
                return await self._update_memory(filename, validated)
            elif action == "delete":
                await self._delete_memory(filename)
                return None
        except Exception as e:
            if self.debug:
                logger.debug(f"[ExtractionWorker] Operation failed: {e}")
        return None

    async def _resolve_metadata(self, op: _Operation) -> MemoryMetadata:
        """Build MemoryMetadata from a validated operation, preferring
        the new domain/kind taxonomy but falling back to legacy type mapping."""
        md = op.metadata or _OpMetadata()
        name = md.name or op.filename.replace(".md", "")

        mem_type = MemoryType(md.type)

        # Prefer explicit domain/kind; otherwise infer from legacy type
        if md.domain:
            domain = MemoryDomain(md.domain)
        else:
            domain = LEGACY_TYPE_MAPPING[mem_type][0]

        if md.kind:
            kind = MemoryKind(md.kind)
        else:
            kind = LEGACY_TYPE_MAPPING[mem_type][1]

        stability = MemoryStability(md.stability)

        return MemoryMetadata(
            name=name,
            description=md.description,
            type=mem_type,
            domain=domain,
            kind=kind,
            confidence=md.confidence,
            importance=md.importance,
            stability=stability,
            tags=md.tags,
        )

    def _find_near_duplicate(self, metadata: MemoryMetadata) -> Optional[str]:
        """
        Find an existing memory that is a near-duplicate of the one about to
        be created, using Jaccard overlap on tags + description words
        (per the memory architecture: overlap > 0.5 -> update instead of
        create). Returns the existing filename if found, else None.
        """
        headers = self.memory_store.scan_memory_files()
        if not headers:
            return None

        new_tokens = self._overlap_tokens(metadata.tags, metadata.description)
        if not new_tokens:
            return None

        best: Optional[str] = None
        best_score = 0.5  # minimum threshold
        for h in headers:
            existing_tokens = self._overlap_tokens(h.tags, h.description)
            if not existing_tokens:
                continue
            union = new_tokens | existing_tokens
            jaccard = len(new_tokens & existing_tokens) / len(union)
            if jaccard > best_score:
                best_score = jaccard
                best = h.filename
        return best

    @staticmethod
    def _overlap_tokens(tags: List[str], description: str) -> set:
        tokens = set(t for t in (tags or []) if t)
        for word in (description or "").lower().split():
            if len(word) > 2:
                tokens.add(word)
        return tokens

    async def _create_memory(
        self, filename: str, op: _Operation
    ) -> Optional[MemoryMetadata]:
        metadata = await self._resolve_metadata(op)
        body = op.body or ""

        # Update-detection: if a near-duplicate already exists, update it
        # instead of creating a redundant file (memory architecture 5.1).
        dup = self._find_near_duplicate(metadata)
        if dup and dup != filename:
            if self.debug:
                logger.debug(
                    f"[ExtractionWorker] Near-duplicate of {dup} detected; "
                    f"updating it instead of creating {filename}"
                )
            return await self._update_memory(dup, op)

        if self.memory_store.write_memory_file(filename, metadata, body):
            return metadata
        return None

    async def _update_memory(
        self, filename: str, op: _Operation
    ) -> Optional[MemoryMetadata]:
        existing = self.memory_store.read_memory_file(
            str(self.memory_store.memory_dir / filename)
        )

        if not existing:
            return await self._create_memory(filename, op)

        metadata, body = existing
        new_body = op.body if op.body else body

        # If the LLM only returned a partial body, ask it to merge explicitly
        if not op.body:
            new_body = await self._merge_body(filename, body, op)

        md = op.metadata
        if md:
            if md.description:
                metadata.description = md.description
            if md.importance is not None:
                metadata.importance = md.importance
            if md.confidence is not None:
                metadata.confidence = md.confidence
            if md.tags:
                metadata.tags = md.tags
            if md.domain:
                metadata.domain = MemoryDomain(md.domain)
            if md.kind:
                metadata.kind = MemoryKind(md.kind)

        metadata.updated = datetime.now()

        if self.memory_store.write_memory_file(filename, metadata, new_body):
            return metadata
        return None

    async def _merge_body(
        self, filename: str, existing_body: str, op: _Operation
    ) -> str:
        """Use an explicit merge prompt when the update op lacks a body."""
        user_prompt = UPDATE_PROMPT.format(
            filename=filename,
            existing_content=existing_body,
            new_info=op.metadata.description if op.metadata else "",
        )
        try:
            response = await self._call_llm_with_retry([
                {"role": "system", "content": "You are a memory update subagent."},
                {"role": "user", "content": user_prompt},
            ])
            return response or existing_body
        except Exception:
            return existing_body

    async def _delete_memory(self, filename: str) -> bool:
        return self.memory_store.delete_memory_file(filename)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "queue_size": self._extraction_queue.qsize(),
            "failed_items": len(self._failed_items),
            "total_extractions": self._extraction_count,
            "last_cursor": self._last_cursor,
        }
