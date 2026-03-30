"""
SimpleMem integration for logicore.

Provides high-performance context engineering:
- Fast embedding-based retrieval (10-50ms target)
- Background memory processing (non-blocking)
- Per-user memory isolation

Based on SimpleMem: https://github.com/aiming-lab/SimpleMem
"""
import asyncio
import uuid
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field
import threading
import queue

from . import config


@dataclass
class MemoryEntry:
    """Atomic memory entry."""
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    lossless_restatement: str = ""
    keywords: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None
    location: Optional[str] = None
    persons: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    topic: Optional[str] = None


@dataclass  
class Dialogue:
    """A single dialogue turn."""
    dialogue_id: int
    speaker: str
    content: str
    timestamp: Optional[str] = None
    
    def __str__(self):
        ts = f"[{self.timestamp}] " if self.timestamp else ""
        return f"{ts}{self.speaker}: {self.content}"


class AgentrySimpleMem:
    """
    SimpleMem integration for logicore.
    
    Features:
    - Per-user memory isolation (separate LanceDB tables)
    - Async-compatible operations
    - Background memory processing
    - Fast embedding-only retrieval
    
    Usage:
        memory = AgentrySimpleMem(user_id="123", session_id="abc")
        
        # On user message - returns relevant context
        contexts = await memory.on_user_message("What did we discuss?")
        
        # On assistant response - queues for processing
        await memory.on_assistant_message("We discussed...")
        
        # Process queued dialogues (call periodically or on session end)
        await memory.process_pending()
    """
    
    def __init__(
        self,
        user_id: str,
        session_id: str = "default",
        max_context_entries: int = 5,
        enable_background_processing: bool = True,
        isolate_by_session: bool = True,
        debug: bool = False
    ):
        self.user_id = user_id
        self.session_id = session_id
        self.max_context_entries = max_context_entries
        self.enable_background = enable_background_processing
        self.isolate_by_session = isolate_by_session
        self.debug = debug
        self.min_store_score = config.get_min_store_score()
        self.min_retrieve_score = config.get_min_retrieve_score()
        self.max_facts_per_turn = config.get_max_facts_per_turn()
        self.max_memory_chars = config.get_max_memory_chars()
        
        # Per-user table name
        self.table_name = config.get_memory_table_name(
            user_id=user_id,
            session_id=session_id,
            isolate_by_session=isolate_by_session
        )
        
        # Components (lazy initialized)
        self._vector_store = None
        self._embedding_model = None
        self._initialized = False
        
        # Dialogue queue for batch processing
        self._dialogue_queue: List[Dialogue] = []
        self._dialogue_counter = 0
        
        # Background processing
        self._processing_lock = threading.Lock()

    def _resolve_table_name(self) -> str:
        return config.get_memory_table_name(
            user_id=self.user_id,
            session_id=self.session_id,
            isolate_by_session=self.isolate_by_session
        )

    def _ensure_table_binding(self):
        expected = self._resolve_table_name()
        if expected == self.table_name:
            return

        if self.debug:
            print(f"[SimpleMem] Switching table: {self.table_name} -> {expected}")

        self.table_name = expected
        self._vector_store = None

        if self._dialogue_queue:
            self._dialogue_queue = []
            if self.debug:
                print("[SimpleMem] Cleared pending dialogue queue due to session table switch")
    
    def _lazy_init(self):
        """Lazy initialization of vector store and embedding model."""
        self._ensure_table_binding()

        if self._initialized and self._vector_store is not None:
            return
        
        try:
            from .embedding import EmbeddingModel
            from .vector_store import VectorStore
            
            if self.debug:
                print(f"[SimpleMem] Initializing for user {self.user_id} (session: {self.session_id})...")
            
            # Initialize embedding model
            if self._embedding_model is None:
                embed_config = config.get_embedding_config()
                self._embedding_model = EmbeddingModel(
                    ollama_base_url=embed_config["ollama_url"]
                )
            
            # Initialize vector store with per-user table
            self._vector_store = VectorStore(
                db_path=config.get_lancedb_path(),
                embedding_model=self._embedding_model,
                table_name=self.table_name
            )
            
            self._initialized = True
            
            if self.debug:
                print(f"[SimpleMem] Ready! Table: {self.table_name}")
                
        except Exception as e:
            print(f"[SimpleMem] Init error: {e}")
            self._initialized = True  # Mark as tried to avoid repeated errors

    def _is_transient_memory_text(self, text: str) -> bool:
        normalized = text.lower().strip()

        transient_patterns = [
            r"\[user\]:\s*remind me",
            r"\[user\]:\s*set (a )?reminder",
            r"\[user\]:\s*remind",
            r"\[assistant\]:\s*(sure thing|of course|okay|alright).*(i('| wi)?ll)\s+remind",
            r"\[assistant\]:.*in\s+\d+\s*(sec|second|seconds|min|minute|minutes)",
            r"\[assistant\]:.*\b(i('| wi)?ll|got it)\b.*\b(remind|reminder|ping|notify|pop)\b",
        ]

        return any(re.search(pattern, normalized) for pattern in transient_patterns)

    def _looks_like_question(self, text: str) -> bool:
        t = text.strip().lower()
        if not t:
            return False
        if "?" in t:
            return True
        return bool(re.match(r"^(what|why|how|when|where|who|can|could|would|should|do|does|did|is|are|will)\b", t))

    def _is_vague_or_smalltalk(self, text: str) -> bool:
        t = text.strip().lower()
        if not t:
            return True

        vague_patterns = [
            r"^thanks[.!]*$",
            r"^okay[.!]*$",
            r"^ok[.!]*$",
            r"^sure[.!]*$",
            r"^got it[.!]*$",
            r"^sounds good[.!]*$",
            r"^nice[.!]*$",
            r"^hello[.!]*$",
            r"^hi[.!]*$",
        ]
        return any(re.match(pattern, t) for pattern in vague_patterns)

    def _score_memory_signal(self, speaker: str, text: str) -> int:
        t = text.strip().lower()
        if not t:
            return 0

        score = 0

        durable_markers = [
            r"\bmy name is\b",
            r"\bi (am|work|use|prefer|need|want|always|usually)\b",
            r"\b(project|repo|codebase|stack|language|framework|database|api)\b",
            r"\b(prefer|preference|constraint|requirement|deadline|timezone)\b",
            r"\b(version|path|port|endpoint|model|provider|environment)\b",
        ]
        if any(re.search(pattern, t) for pattern in durable_markers):
            score += 2

        if re.search(r"\b(scheduled|created|added|saved|updated|deleted|fixed|resolved|next run|job id)\b", t):
            score += 2

        if re.search(r"\d", t):
            score += 1

        token_count = len(re.findall(r"\b\w+\b", t))
        if 5 <= token_count <= 40:
            score += 1
        elif token_count > 80:
            score -= 1

        if self._looks_like_question(t):
            score -= 2

        if self._is_vague_or_smalltalk(t):
            score -= 2

        if self._is_transient_memory_text(t):
            score -= 3

        if speaker.lower() == "assistant":
            if re.search(r"\b(i('| wi)?ll|i can)\b", t) and not re.search(r"\b(done|completed|scheduled|created|saved|updated)\b", t):
                score -= 2

        return max(score, 0)

    def _extract_atomic_facts(self, dialogue: Dialogue) -> List[str]:
        text = dialogue.content.strip()
        if not text:
            return []

        candidate_lines = [line.strip(" -\t") for line in re.split(r"[\n\r]+", text) if line.strip()]
        facts: List[str] = []

        for line in candidate_lines:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
            if not sentences:
                sentences = [line]

            for sentence in sentences:
                cleaned = sentence.strip()
                if not cleaned:
                    continue
                if len(cleaned) > self.max_memory_chars:
                    cleaned = cleaned[: self.max_memory_chars].rstrip() + "..."

                score = self._score_memory_signal(dialogue.speaker, cleaned)
                if score < self.min_store_score:
                    continue

                if self._looks_like_question(cleaned):
                    continue

                if self._is_transient_memory_text(f"[{dialogue.speaker}]: {cleaned}"):
                    continue

                facts.append(cleaned)
                if len(facts) >= self.max_facts_per_turn:
                    return facts

        return facts

    def _parse_score_from_memory_text(self, memory_text: str) -> int:
        m = re.search(r"\[score=(\d+)\]", memory_text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return 0

    def _format_memory_text(self, dialogue: Dialogue, fact: str, score: int) -> str:
        speaker = dialogue.speaker.capitalize()
        return f"[{speaker}][score={score}] {fact}"

    def _should_store_dialogue(self, dialogue: Dialogue) -> bool:
        text = dialogue.content.strip()
        if not text:
            return False

        normalized = text.lower()
        if dialogue.speaker.lower() == "user":
            if re.search(r"^\s*(remind me|set (a )?reminder|in next \d+\s*(sec|second|seconds|min|minute|minutes))", normalized):
                return False

        if dialogue.speaker.lower() == "assistant":
            promissory = re.search(r"\b(i('| wi)?ll|i can|got it)\b.*\b(remind|reminder|schedule|notify|ping|pop)\b", normalized)
            action_confirmed = re.search(r"\b(scheduled|created|added|next run|job id|done|completed|triggered)\b", normalized)
            if promissory and not action_confirmed:
                return False

        if self._is_vague_or_smalltalk(normalized):
            return False

        if self._looks_like_question(normalized):
            return False

        if self._score_memory_signal(dialogue.speaker, text) < self.min_store_score:
            return False

        return True
    
    async def on_user_message(self, content: str) -> List[str]:
        """
        Called when user sends a message.
        
        Returns relevant context for LLM augmentation.
        Queues the message for memory processing.
        """
        # Queue dialogue for processing
        self._queue_dialogue("User", content)
        
        # Fast retrieval (embedding-only, no LLM)
        contexts = self._fast_retrieve(content)
        
        if self.debug and contexts:
            print(f"[SimpleMem] Retrieved {len(contexts)} memories")
        
        return contexts
    
    async def on_assistant_message(self, content: str):
        """
        Called when assistant responds.
        Queues the response for memory processing.
        """
        self._queue_dialogue("Assistant", content)
    
    def _queue_dialogue(self, speaker: str, content: str):
        """Add dialogue to processing queue."""
        self._dialogue_counter += 1
        dialogue = Dialogue(
            dialogue_id=self._dialogue_counter,
            speaker=speaker,
            content=content,
            timestamp=datetime.now().isoformat()
        )
        self._dialogue_queue.append(dialogue)
        
        if self.debug:
            print(f"[SimpleMem] Queued: [{speaker}] {content[:50]}...")
    
    def _fast_retrieve(self, query: str, limit: int = None) -> List[str]:
        """
        Pure embedding retrieval - NO LLM calls.
        Target latency: 10-50ms
        """
        self._lazy_init()
        
        if not self._vector_store:
            return []
        
        limit = limit or self.max_context_entries
        
        try:
            results = self._vector_store.semantic_search(query, top_k=limit)
            filtered: List[str] = []
            seen = set()

            for row in results:
                memory_text = (row.lossless_restatement or "").strip()
                if not memory_text:
                    continue
                if self._is_transient_memory_text(memory_text):
                    continue

                if self._parse_score_from_memory_text(memory_text) < self.min_retrieve_score:
                    continue

                canonical = memory_text.lower()
                if canonical in seen:
                    continue
                seen.add(canonical)
                filtered.append(memory_text)

            return filtered[:limit]
        except Exception as e:
            if self.debug:
                print(f"[SimpleMem] Retrieval error: {e}")
            return []
    
    async def process_pending(self):
        """
        Process pending dialogues and store as memories.
        
        For now, stores dialogues directly (simplified approach).
        Full SimpleMem uses LLM-based atomic extraction.
        """
        if not self._dialogue_queue:
            return
        
        self._lazy_init()
        
        if not self._vector_store:
            self._dialogue_queue = []
            return
        
        with self._processing_lock:
            dialogues = self._dialogue_queue.copy()
            self._dialogue_queue = []
        
        try:
            if self.debug:
                print(f"[SimpleMem] Processing {len(dialogues)} dialogues...")
            
            # Convert dialogues to memory entries (simplified)
            entries = []
            for dialogue in dialogues:
                if not self._should_store_dialogue(dialogue):
                    continue
                facts = self._extract_atomic_facts(dialogue)
                for fact in facts:
                    score = self._score_memory_signal(dialogue.speaker, fact)
                    if score < self.min_store_score:
                        continue

                    entry = MemoryEntry(
                        lossless_restatement=self._format_memory_text(dialogue, fact, score),
                        keywords=self._extract_keywords(fact),
                        timestamp=dialogue.timestamp,
                        persons=[dialogue.speaker] if dialogue.speaker else [],
                    )
                    entries.append(entry)
            
            # Store to vector store
            if entries:
                self._vector_store.add_entries(entries)
            
            if self.debug:
                print(f"[SimpleMem] Stored {len(entries)} memories")
                
        except Exception as e:
            if self.debug:
                print(f"[SimpleMem] Processing error: {e}")
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Simple keyword extraction (no LLM)."""
        import re
        
        # Common stop words
        stop_words = {
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i',
            'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at',
            'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her',
            'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there',
            'their', 'what', 'so', 'up', 'out', 'if', 'about', 'who', 'get',
            'which', 'go', 'me', 'is', 'are', 'was', 'were', 'been', 'being',
        }
        
        words = re.findall(r'\b\w+\b', text.lower())
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]
        return keywords[:10]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        self._lazy_init()
        
        stats = {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "table_name": self.table_name,
            "initialized": self._initialized,
            "pending_dialogues": len(self._dialogue_queue),
        }
        
        if self._vector_store:
            try:
                all_entries = self._vector_store.get_all_entries()
                stats["total_memories"] = len(all_entries)
            except:
                stats["total_memories"] = "unknown"
        
        return stats
    
    def clear_memories(self):
        """Clear all memories for this user."""
        self._lazy_init()
        
        if self._vector_store:
            self._vector_store.clear()
            if self.debug:
                print(f"[SimpleMem] Cleared memories for {self.user_id}")
