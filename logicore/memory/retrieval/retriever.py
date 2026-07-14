"""
Memory retrieval and ranking system.

Implements topic detection, decay scoring, and cluster-based
retrieval for relevant memories.
"""

import math
import re
import json
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
import logging

from pydantic import BaseModel, Field, ValidationError

from logicore.memory.types import (
    MemoryHeader,
    MemoryDomain,
    MemoryKind,
    MemoryStability,
    MemoryScore,
    TopicDetection,
    DOMAIN_KEYWORDS,
    STABILITY_HALF_LIFE_DAYS,
    STABILITY_MAX_AGE_DAYS,
)
from logicore.memory.storage import MemoryStore

logger = logging.getLogger(__name__)


# Valid set of domain values for LLM-returned topic detection
_VALID_DOMAIN_VALUES = {d.value for d in MemoryDomain}
_VALID_INTENTS = {"question", "task", "recall", "general"}


class _LLMTopicResult(BaseModel):
    """Validated schema for LLM-based topic detection output."""

    primary_domain: str = Field(..., description="Primary memory domain")
    secondary_domains: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    intent: str = Field(default="general")

    def to_topic_detection(self) -> TopicDetection:
        primary = (
            MemoryDomain(self.primary_domain)
            if self.primary_domain in _VALID_DOMAIN_VALUES
            else MemoryDomain.KNOWLEDGE
        )
        secondary = []
        for d in self.secondary_domains:
            if d in _VALID_DOMAIN_VALUES and MemoryDomain(d) != primary:
                secondary.append(MemoryDomain(d))
        intent = self.intent if self.intent in _VALID_INTENTS else "general"
        return TopicDetection(
            primary_domain=primary,
            secondary_domains=secondary,
            keywords=self.keywords,
            intent=intent,
        )


_TOPIC_DETECTION_PROMPT = """You are a query classifier for a persistent memory system.

Given the user query, determine:
1. primary_domain: ONE of {domains}
2. secondary_domains: zero or more of {domains} that are also relevant
3. keywords: key terms from the query useful for matching memories
4. intent: one of question | task | recall | general

Respond ONLY with a JSON object matching this schema:
{{"primary_domain": str, "secondary_domains": [str], "keywords": [str], "intent": str}}
""".format(domains=", ".join(sorted(_VALID_DOMAIN_VALUES)))

# Budget allocation per turn
MAX_FILES_PER_TURN = 5
PRIMARY_DOMAIN_BUDGET = 3
SECONDARY_DOMAIN_BUDGET = 1
OVERFLOW_BUDGET = 1

# Per-file cap (bytes)
MAX_FILE_SIZE_BYTES = 4096


def detect_topic(query: str) -> TopicDetection:
    """
    Detect user intent and relevant domains from query text.

    Uses keyword matching against domain-specific vocabularies.
    No LLM call - fast stage 1 detection.
    """
    query_lower = query.lower()

    # Score each domain
    domain_scores: Dict[MemoryDomain, int] = {}
    matched_keywords: Dict[MemoryDomain, List[str]] = {}

    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = 0
        matches = []
        for kw in keywords:
            if kw in query_lower:
                score += 1
                matches.append(kw)
        if score > 0:
            domain_scores[domain] = score
            matched_keywords[domain] = matches

    # Determine primary domain
    if domain_scores:
        primary_domain = max(domain_scores, key=lambda d: domain_scores[d])
        secondary_domains = [d for d in domain_scores if d != primary_domain]
    else:
        primary_domain = MemoryDomain.KNOWLEDGE
        secondary_domains = []

    # Detect intent
    intent = "general"
    if any(w in query_lower for w in ["who", "what", "when", "where", "why", "how"]):
        intent = "question"
    elif any(
        w in query_lower for w in ["do", "make", "create", "build", "fix", "update"]
    ):
        intent = "task"
    elif any(w in query_lower for w in ["recall", "remember", "what was", "remind"]):
        intent = "recall"

    # Collect all matched keywords
    all_keywords = []
    for kw_list in matched_keywords.values():
        all_keywords.extend(kw_list)

    return TopicDetection(
        primary_domain=primary_domain,
        secondary_domains=secondary_domains,
        keywords=all_keywords,
        intent=intent,
    )


async def detect_topic_llm(
    query: str, llm_provider: Any
) -> Optional[TopicDetection]:
    """
    Detect intent and domains from a query using an LLM.

    Falls back to None on any failure so the caller can use keyword matching.
    """
    try:
        response = await llm_provider.chat([
            {"role": "system", "content": _TOPIC_DETECTION_PROMPT},
            {"role": "user", "content": f"Query: {query}"},
        ])
        content = response.content if hasattr(response, "content") else str(response)
        if not content:
            return None

        # Extract JSON (handles code fences)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        parsed = _LLMTopicResult.model_validate(data)
        return parsed.to_topic_detection()
    except (ValidationError, json.JSONDecodeError, Exception) as e:
        logger.debug(f"[detect_topic_llm] failed, falling back to keywords: {e}")
        return None


def compute_decay_score(
    mtime_ms: float,
    importance: float,
    stability: MemoryStability,
) -> float:
    """
    Compute decay score for a memory based on age, importance, and stability.

    Formula:
        baseDecay = 0.5 ^ (ageDays / halfLife[stability])
        floor = 0.3 + importance * 0.4
        decayScore = max(baseDecay, floor)
    """
    now_ms = datetime.now().timestamp() * 1000
    age_days = max(0, (now_ms - mtime_ms) / (1000 * 86400))

    half_life = STABILITY_HALF_LIFE_DAYS.get(stability, 90)

    # Handle infinite half-life for stable memories
    if half_life == float("inf"):
        base_decay = 1.0
    else:
        base_decay = math.pow(0.5, age_days / half_life)

    # Floor based on importance
    floor = 0.3 + importance * 0.4

    return max(base_decay, floor)


def compute_ranking_score(
    decay: float,
    importance: float,
    confidence: float,
) -> float:
    """
    Compute composite ranking score.

    Formula:
        rankingScore = decay * 0.6 + importance * 0.25 + confidence * 0.15
    """
    return decay * 0.6 + importance * 0.25 + confidence * 0.15


def should_forget(
    mtime_ms: float,
    stability: MemoryStability,
    expires_at: Optional[datetime] = None,
) -> bool:
    """
    Check if a memory should be auto-forgotten based on age and stability.
    """
    if expires_at and datetime.now() >= expires_at:
        return True

    now_ms = datetime.now().timestamp() * 1000
    age_days = (now_ms - mtime_ms) / (1000 * 86400)

    max_age = STABILITY_MAX_AGE_DAYS.get(stability, 730)

    # Stable memories are never auto-forgotten
    if stability == MemoryStability.STABLE:
        return False

    return age_days > max_age


class MemoryRetriever:
    """
    Retrieval system for relevant memories.

    Implements multi-stage retrieval:
    1. Topic detection (fast, no LLM)
    2. Cluster retrieval with scoring
    3. Optional LLM selection (for high-stakes queries)
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        debug: bool = False,
        llm_provider: Any = None,
    ):
        self.memory_store = memory_store
        self.debug = debug
        self.llm_provider = llm_provider
        self._surfaced_this_session: set = set()
        self._session_bytes = 0
        self._max_session_bytes = 60000  # 60KB per session

    def reset_session(self) -> None:
        """Reset session state (e.g., after compact)."""
        self._surfaced_this_session.clear()
        self._session_bytes = 0

    def _score_headers(self, topic: TopicDetection) -> List[MemoryScore]:
        """Score all candidate headers against the detected topic."""
        headers = self.memory_store.scan_memory_files()
        scored: List[MemoryScore] = []
        for header in headers:
            if header.filename in self._surfaced_this_session:
                continue
            if should_forget(
                header.mtime_ms,
                header.stability or MemoryStability.EVOLVING,
                header.expires_at,
            ):
                continue

            importance = header.importance or 0.5
            confidence = header.confidence or 0.7
            stability = header.stability or MemoryStability.EVOLVING

            decay = compute_decay_score(header.mtime_ms, importance, stability)

            # Decay-based forgetting (per architecture: drop decayScore < 0.2)
            if decay < 0.2:
                continue

            ranking = compute_ranking_score(decay, importance, confidence)

            domain_match = False
            if header.domain == topic.primary_domain:
                ranking *= 1.5
                domain_match = True
            elif header.domain in topic.secondary_domains:
                ranking *= 1.2
                domain_match = True

            scored.append(
                MemoryScore(
                    header=header,
                    decay_score=decay,
                    ranking_score=ranking,
                    domain_match=domain_match,
                )
            )
        return scored

    def retrieve(
        self,
        query: str,
        budget: int = MAX_FILES_PER_TURN,
        use_llm_selection: bool = False,
    ) -> List[Tuple[MemoryHeader, str, float]]:
        """
        Synchronous retrieval (keyword-based topic detection only).

        LLM selection is not performed in the sync path. For full LLM-based
        topic detection and selection, use :meth:`aretrieve`.
        """
        if self._session_bytes >= self._max_session_bytes:
            return []

        topic = detect_topic(query)
        scored = self._score_headers(topic)
        return self._collect(topic, scored, budget)

    async def aretrieve(
        self,
        query: str,
        budget: int = MAX_FILES_PER_TURN,
        use_llm_selection: bool = False,
    ) -> List[Tuple[MemoryHeader, str, float]]:
        """
        Async retrieval with optional LLM-based topic detection and selection.

        Args:
            query: User query text
            budget: Max number of files to return
            use_llm_selection: Whether to use LLM for final selection

        Returns:
            List of (header, body, ranking_score) tuples
        """
        if self._session_bytes >= self._max_session_bytes:
            return []

        topic = None
        if self.llm_provider:
            topic = await detect_topic_llm(query, self.llm_provider)
        if topic is None:
            topic = detect_topic(query)

        scored = self._score_headers(topic)

        if use_llm_selection and self.llm_provider and scored:
            scored = await self._llm_select(query, scored, budget)

        return self._collect(topic, scored, budget)

    def _collect(
        self,
        topic: TopicDetection,
        scored: List[MemoryScore],
        budget: int,
    ) -> List[Tuple[MemoryHeader, str, float]]:
        """Collect memories within budget from scored candidates."""
        if not scored:
            return []

        # Sort by ranking score
        scored.sort(key=lambda s: s.ranking_score, reverse=True)

        # Allocate budget
        results: List[Tuple[MemoryHeader, str, float]] = []
        primary_count = 0
        secondary_count = 0
        overflow_count = 0

        for score in scored:
            if len(results) >= budget:
                break

            # Check file size budget
            file_size = len(score.header.description.encode("utf-8"))
            if self._session_bytes + file_size > self._max_session_bytes:
                break

            # Read the actual file content
            file_content = self.memory_store.read_memory_file(score.header.file_path)
            if not file_content:
                continue

            metadata, body = file_content

            # Enforce per-file byte cap
            encoded = body.encode("utf-8")
            if len(encoded) > MAX_FILE_SIZE_BYTES:
                body = encoded[:MAX_FILE_SIZE_BYTES].decode("utf-8", errors="ignore")

            # Budget allocation
            if score.header.domain == topic.primary_domain:
                if primary_count >= PRIMARY_DOMAIN_BUDGET:
                    continue
                primary_count += 1
            elif score.header.domain in topic.secondary_domains:
                if secondary_count >= SECONDARY_DOMAIN_BUDGET:
                    continue
                secondary_count += 1
            else:
                if overflow_count >= OVERFLOW_BUDGET:
                    continue
                overflow_count += 1

            results.append((score.header, body, score.ranking_score))
            self._surfaced_this_session.add(score.header.filename)
            self._session_bytes += len(body.encode("utf-8"))

        if self.debug:
            logger.debug(
                f"[MemoryRetriever] Retrieved {len(results)} memories "
                f"(session_bytes={self._session_bytes})"
            )

        return results

    async def _llm_select(
        self,
        query: str,
        scored: List[MemoryScore],
        budget: int,
    ) -> List[MemoryScore]:
        """
        Use the LLM to re-rank / select the most relevant memories.

        The LLM is given candidate summaries and returns the filenames it
        considers relevant (up to `budget`). Returns the original scored list
        unchanged on any failure.
        """
        candidates = []
        for s in scored:
            h = s.header
            domain = h.domain.value if h.domain else "unknown"
            kind = h.kind.value if h.kind else "unknown"
            candidates.append(
                f"- {h.filename} [{domain}/{kind}] {h.description}"
            )
        if not candidates:
            return scored

        prompt = (
            "Given the user query, select the memory files most relevant to "
            "answering or personalizing it. Return ONLY a JSON array of "
            "filenames you selected, in order of relevance (max "
            f"{budget} items).\n\n"
            f"QUERY: {query}\n\nMEMORIES:\n" + "\n".join(candidates)
        )
        try:
            response = await self.llm_provider.chat([
                {"role": "system", "content": "You are a memory relevance selector."},
                {"role": "user", "content": prompt},
            ])
            content = response.content if hasattr(response, "content") else str(response)
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if not match:
                return scored
            selected = json.loads(match.group(0))
            if not isinstance(selected, list):
                return scored

            order = {name: i for i, name in enumerate(selected)}
            selected_set = set(order)
            kept = [s for s in scored if s.header.filename in selected_set]
            # Apply LLM priority ordering, then by original score
            kept.sort(key=lambda s: (order.get(s.header.filename, 999), -s.ranking_score))
            # Boost selected items so they survive budget allocation
            for s in kept:
                s.ranking_score = max(s.ranking_score, 100.0)
            return kept
        except Exception as e:
            logger.debug(f"[MemoryRetriever._llm_select] failed: {e}")
            return scored

    def format_for_injection(
        self,
        memories: List[Tuple[MemoryHeader, str, float]],
    ) -> str:
        """
        Format retrieved memories for context injection.

        Returns:
            Formatted string for system-reminder injection
        """
        if not memories:
            return ""

        lines = [
            "## Relevant Memories",
            "(Recalled from persistent memory and provided inline below. This is "
            "NOT a file on disk — do NOT use read_file/search_files/list_files to "
            "look it up. Use only the content shown here.)",
            "",
        ]

        for header, body, score in memories:
            domain = header.domain.value if header.domain else "unknown"
            kind = header.kind.value if header.kind else "unknown"
            tags = f" tags={', '.join(header.tags)}" if header.tags else ""
            related_to = (
                f" related={', '.join(header.related_to)}" if header.related_to else ""
            )
            expires_text = (
                f" expires={header.expires_at.isoformat()}" if header.expires_at else ""
            )

            # Age text
            age_days = (datetime.now().timestamp() * 1000 - header.mtime_ms) / (
                1000 * 86400
            )
            if age_days < 1:
                age_text = "today"
            elif age_days < 7:
                age_text = f"{int(age_days)} days ago"
            elif age_days < 30:
                age_text = f"{int(age_days / 7)} weeks ago"
            else:
                age_text = f"{int(age_days / 30)} months ago"

            lines.append(
                f"Memory [{domain}/{kind}, relevance {score:.2f}] "
                f"saved {age_text}{expires_text}{tags}{related_to}:"
            )
            lines.append(body.strip())
            lines.append("")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get retrieval statistics."""
        return {
            "surfaced_this_session": len(self._surfaced_this_session),
            "session_bytes": self._session_bytes,
            "max_session_bytes": self._max_session_bytes,
        }
