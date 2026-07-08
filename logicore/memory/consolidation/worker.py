"""
Memory consolidation and forgetting mechanism.

Implements background consolidation (autoDream) that merges, prunes,
and deduplicates memory files across sessions.
"""

import asyncio
import json
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from logicore.memory.types import (
    MemoryHeader, MemoryMetadata, MemoryStability,
)
from logicore.memory.storage import MemoryStore
from logicore.memory.retrieval.retriever import compute_decay_score
from logicore.providers.policies import (
    RetryPolicy,
    _classify_failure_standalone,
)

logger = logging.getLogger(__name__)

# Consolidation prompt
CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation agent. Your task is to review all memory files and:

1. ORIENT: Review the memory directory structure and existing files
2. GATHER: Read all memory files and identify:
   - Duplicate or near-duplicate memories
   - Contradictory information
   - Stale or outdated memories
   - Related memories that could be merged
3. CONSOLIDATE: For each issue found:
   - Merge duplicates into a single, comprehensive memory
   - Resolve contradictions (prefer newer information)
   - Update stale memories with current information
   - Update timestamps and stability levels
4. PRUNE: Remove memories that are:
   - Too old and low importance
   - No longer relevant
   - Duplicates after merging

OUTPUT FORMAT:
Return a JSON object with operations:
{
    "merge": [{"target": "file1.md", "sources": ["file2.md", "file3.md"], "merged_content": "..."}],
    "update": [{"filename": "file.md", "content": "..."}],
    "delete": ["stale_file.md"],
    "create": [{"filename": "new_file.md", "metadata": {...}, "body": "..."}]
}

If no consolidation is needed, return {"merge": [], "update": [], "delete": [], "create": []}
"""

CONSOLIDATION_USER_PROMPT = """Review and consolidate all memory files.

MEMORY DIRECTORY CONTENTS:
{memory_list}

EXISTING MEMORY FILES:
{memory_files}

Perform consolidation and return operations as JSON.
"""

# Gate thresholds
MIN_HOURS_BETWEEN_CONSOLIDATIONS = 24
MIN_SESSIONS_BETWEEN_CONSOLIDATIONS = 5


class ConsolidationWorker:
    """
    Background worker for memory consolidation.
    
    Implements the autoDream mechanism that reviews all memory files,
    merges duplicates, resolves contradictions, and prunes stale entries.
    """
    
    def __init__(
        self,
        memory_store: MemoryStore,
        llm_provider: Any = None,
        debug: bool = False,
    ):
        """
        Initialize the consolidation worker.
        
        Args:
            memory_store: Memory store to consolidate
            llm_provider: LLM provider for consolidation (optional)
            debug: Enable debug logging
        """
        self.memory_store = memory_store
        self.llm_provider = llm_provider
        self.debug = debug
        self._retry_policy = RetryPolicy(max_attempts=3)
        self._last_consolidation: Optional[float] = None
        self._session_count: int = 0
        self._running: bool = False

    async def _chat_with_retry(
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
                            f"[ConsolidationWorker] LLM call terminal failure "
                            f"({category}): {e}"
                        )
                    return None
                delay = self._retry_policy.calculate_delay(attempt, category)
                if self.debug:
                    logger.debug(
                        f"[ConsolidationWorker] LLM call retry {attempt} "
                        f"after {delay:.1f}s ({category}): {e}"
                    )
                await asyncio.sleep(delay)
                attempt += 1
    
    def should_consolidate(self) -> bool:
        """
        Check if consolidation should run.
        
        Gates:
        1. Time gate: Hours since last consolidation >= MIN_HOURS
        2. Session gate: Session count >= MIN_SESSIONS
        """
        # Time gate
        if self._last_consolidation:
            hours_since = (time.time() - self._last_consolidation) / 3600
            if hours_since < MIN_HOURS_BETWEEN_CONSOLIDATIONS:
                return False
        
        # Session gate
        if self._session_count < MIN_SESSIONS_BETWEEN_CONSOLIDATIONS:
            return False
        
        return True
    
    def record_session(self) -> None:
        """Record a new session for gate tracking."""
        self._session_count += 1
    
    async def consolidate(self) -> Dict[str, Any]:
        """
        Run memory consolidation.
        
        Returns:
            Statistics about consolidation operations
        """
        if self._running:
            return {"status": "already_running"}
        
        self._running = True
        stats = {
            "merged": 0,
            "updated": 0,
            "deleted": 0,
            "created": 0,
            "errors": 0,
        }
        
        try:
            # Scan all memory files
            headers = self.memory_store.scan_memory_files(force_refresh=True)
            
            if not headers:
                stats["status"] = "no_memories"
                return stats
            
            # Read all memory files
            memory_files = {}
            for header in headers:
                result = self.memory_store.read_memory_file(header.file_path)
                if result:
                    metadata, body = result
                    memory_files[header.filename] = {
                        "metadata": metadata,
                        "body": body,
                    }
            
            if not memory_files:
                stats["status"] = "no_readable_memories"
                return stats
            
            # Build memory list for prompt
            memory_list = self._build_memory_list(headers)
            memory_content = self._format_memory_files(memory_files)
            
            # Use LLM for consolidation if available
            if self.llm_provider:
                operations = await self._llm_consolidate(
                    memory_list, memory_content, memory_files
                )
            else:
                # Basic rule-based consolidation
                operations = self._rule_based_consolidate(memory_files)
            
            # Execute operations
            if operations:
                stats = await self._execute_operations(operations, stats)
            
            # Update index
            self.memory_store.update_index()
            
            # Record consolidation time
            self._last_consolidation = time.time()
            self._session_count = 0
            
            stats["status"] = "completed"
            
        except Exception as e:
            if self.debug:
                logger.debug(f"[ConsolidationWorker] Consolidation failed: {e}")
            stats["status"] = "failed"
            stats["errors"] += 1
        
        finally:
            self._running = False
        
        return stats
    
    def _build_memory_list(self, headers: List[MemoryHeader]) -> str:
        """Build a list of memory files for the consolidation prompt."""
        lines = []
        for h in headers:
            domain = h.domain.value if h.domain else "unknown"
            kind = h.kind.value if h.kind else "unknown"
            stability = h.stability.value if h.stability else "unknown"
            importance = h.importance if h.importance is not None else 0.5
            age_days = (time.time() * 1000 - h.mtime_ms) / (1000 * 86400)
            lines.append(
                f"- {h.filename} [{domain}/{kind}] "
                f"(importance={importance}, stability={stability}, "
                f"age={age_days:.1f}d): {h.description}"
            )
        return "\n".join(lines)
    
    def _format_memory_files(self, memory_files: Dict[str, Dict], cap_chars: int = 4000) -> str:
        """Format memory file contents for the consolidation prompt.

        Bodies are capped per-file to avoid context overflow, but the cap is
        raised from the previous 2000 chars so relevant detail is retained.
        """
        parts = []
        for filename, data in memory_files.items():
            metadata = data["metadata"]
            body = data["body"]
            if len(body) > cap_chars:
                body = body[:cap_chars] + "\n...[truncated]"
            parts.append(f"### {filename}\n{body}\n")
        return "\n".join(parts)
    
    async def _llm_consolidate(
        self,
        memory_list: str,
        memory_content: str,
        memory_files: Dict[str, Dict],
    ) -> Optional[Dict[str, Any]]:
        """Use LLM for consolidation."""
        user_prompt = CONSOLIDATION_USER_PROMPT.format(
            memory_list=memory_list,
            memory_files=memory_content,
        )
        
        try:
            content = await self._chat_with_retry([
                {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            if not content:
                return None
            return self._parse_consolidation_response(content)

        except Exception as e:
            if self.debug:
                logger.debug(f"[ConsolidationWorker] LLM consolidation failed: {e}")
            return None
    
    def _parse_consolidation_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse LLM consolidation response."""
        try:
            import re
            # Extract JSON from response
            code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
            if code_block:
                return json.loads(code_block.group(1))
            
            # Try raw JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            
        except json.JSONDecodeError:
            pass
        
        return None
    
    def _rule_based_consolidate(
        self,
        memory_files: Dict[str, Dict],
    ) -> Dict[str, Any]:
        """Basic rule-based consolidation (no LLM)."""
        operations = {
            "merge": [],
            "update": [],
            "delete": [],
            "create": [],
        }
        
        # Find exact duplicates (same description)
        seen_descriptions: Dict[str, str] = {}
        for filename, data in memory_files.items():
            desc = data["metadata"].description
            if desc in seen_descriptions:
                # Mark for deletion (keep the first one)
                operations["delete"].append(filename)
            else:
                seen_descriptions[desc] = filename
        
        return operations
    
    async def _execute_operations(
        self,
        operations: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute consolidation operations."""
        # Handle merges
        for merge_op in operations.get("merge", []):
            try:
                target = merge_op.get("target")
                sources = merge_op.get("sources", [])
                merged_content = merge_op.get("merged_content", "")
                
                if not target or not merged_content:
                    continue
                
                # Read target metadata
                target_result = self.memory_store.read_memory_file(
                    str(self.memory_store.memory_dir / target)
                )
                if not target_result:
                    continue
                
                metadata, _ = target_result
                metadata.updated = datetime.now()
                
                # Write merged content
                self.memory_store.write_memory_file(target, metadata, merged_content)
                stats["merged"] += 1
                
                # Delete sources
                for source in sources:
                    self.memory_store.delete_memory_file(source)
                    stats["deleted"] += 1
                    
            except Exception as e:
                if self.debug:
                    logger.debug(f"[ConsolidationWorker] Merge failed: {e}")
                stats["errors"] += 1
        
        # Handle updates
        for update_op in operations.get("update", []):
            try:
                filename = update_op.get("filename")
                content = update_op.get("content")
                
                if not filename or not content:
                    continue
                
                result = self.memory_store.read_memory_file(
                    str(self.memory_store.memory_dir / filename)
                )
                if not result:
                    continue
                
                metadata, _ = result
                metadata.updated = datetime.now()
                
                self.memory_store.write_memory_file(filename, metadata, content)
                stats["updated"] += 1
                
            except Exception as e:
                if self.debug:
                    logger.debug(f"[ConsolidationWorker] Update failed: {e}")
                stats["errors"] += 1
        
        # Handle deletes
        for filename in operations.get("delete", []):
            try:
                if self.memory_store.delete_memory_file(filename):
                    stats["deleted"] += 1
            except Exception as e:
                if self.debug:
                    logger.debug(f"[ConsolidationWorker] Delete failed: {e}")
                stats["errors"] += 1
        
        # Handle creates
        for create_op in operations.get("create", []):
            try:
                filename = create_op.get("filename")
                metadata_dict = create_op.get("metadata", {})
                body = create_op.get("body", "")
                
                if not filename:
                    continue
                
                from logicore.memory.types import MemoryType, MemoryDomain, MemoryKind
                
                type_str = metadata_dict.get("type", "project")
                try:
                    mem_type = MemoryType(type_str)
                except ValueError:
                    mem_type = MemoryType.PROJECT
                
                metadata = MemoryMetadata(
                    name=metadata_dict.get("name", filename.replace('.md', '')),
                    description=metadata_dict.get("description", ""),
                    type=mem_type,
                    domain=MemoryDomain(metadata_dict.get("domain", "knowledge")),
                    kind=MemoryKind(metadata_dict.get("kind", "context")),
                    confidence=metadata_dict.get("confidence", 0.7),
                    importance=metadata_dict.get("importance", 0.5),
                    stability=MemoryStability(metadata_dict.get("stability", "evolving")),
                    tags=metadata_dict.get("tags", []),
                )
                
                if self.memory_store.write_memory_file(filename, metadata, body):
                    stats["created"] += 1
                    
            except Exception as e:
                if self.debug:
                    logger.debug(f"[ConsolidationWorker] Create failed: {e}")
                stats["errors"] += 1
        
        return stats
    
    def forget_stale(self) -> int:
        """
        Remove memories that are past their max age or have decayed below
        the relevance floor (decayScore < 0.2), per the memory architecture.

        Returns:
            Number of memories forgotten
        """
        headers = self.memory_store.scan_memory_files()
        forgotten = 0

        for header in headers:
            stability = header.stability or MemoryStability.EVOLVING
            age_forget = should_forget(header.mtime_ms, stability)
            importance = header.importance or 0.5
            confidence = header.confidence or 0.7
            decay = compute_decay_score(header.mtime_ms, importance, stability)
            decay_forget = decay < 0.2 and stability != MemoryStability.STABLE

            if age_forget or decay_forget:
                if self.memory_store.delete_memory_file(header.filename):
                    forgotten += 1

        if forgotten > 0:
            self.memory_store.update_index()

        return forgotten


def should_forget(mtime_ms: float, stability: MemoryStability) -> bool:
    """Check if a memory should be auto-forgotten."""
    from logicore.memory.types import STABILITY_MAX_AGE_DAYS
    
    now_ms = datetime.now().timestamp() * 1000
    age_days = (now_ms - mtime_ms) / (1000 * 86400)
    
    max_age = STABILITY_MAX_AGE_DAYS.get(stability, 730)
    
    if stability == MemoryStability.STABLE:
        return False
    
    return age_days > max_age
