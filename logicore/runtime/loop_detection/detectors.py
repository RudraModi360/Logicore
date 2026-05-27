"""
Loop Detectors: Pluggable strategies for detecting different loop types.

Detectors:
- ConsecutiveToolCallDetector: Hash-based identical tool call detection
- ContentRepetitionDetector: Chunk-based streaming content analysis
- StagnantStateDetector: No-progress detection
- ToolResultSimilarityDetector: Embedding-based result comparison

Each detector implements the LoopDetector interface and can be
registered with the LoopDetectionEngine.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any

from logicore.runtime.loop_detection.engine import (
    AgentEvent,
    AgentEventType,
    LoopDetectionResult,
    LoopType,
)


class LoopDetector(ABC):
    """Base class for loop detectors."""
    
    @abstractmethod
    async def check(
        self,
        event: AgentEvent,
        session_id: str,
    ) -> LoopDetectionResult:
        """
        Check an event for loop conditions.
        
        Args:
            event: Agent event to analyze
            session_id: Session identifier for state tracking
        
        Returns:
            LoopDetectionResult with detection status
        """
        pass
    
    @abstractmethod
    def reset_session(self, session_id: str) -> None:
        """Reset detector state for a session."""
        pass


@dataclass
class ConsecutiveToolCallState:
    """State for tracking consecutive tool calls."""
    last_hash: Optional[str] = None
    repetition_count: int = 0
    last_tool_name: Optional[str] = None
    last_tool_args: Optional[Dict[str, Any]] = None


class ConsecutiveToolCallDetector(LoopDetector):
    """
    Detects consecutive identical tool calls.
    
    Uses hash-based comparison for efficiency. Triggers when the same
    tool is called with identical arguments N times in a row.
    
    Inspired by gemini-cli's TOOL_CALL_LOOP_THRESHOLD.
    """
    
    def __init__(self, threshold: int = 5):
        """
        Args:
            threshold: Number of identical consecutive calls to trigger detection
        """
        self.threshold = threshold
        self._states: Dict[str, ConsecutiveToolCallState] = {}
    
    def _get_state(self, session_id: str) -> ConsecutiveToolCallState:
        """Get or create state for session."""
        if session_id not in self._states:
            self._states[session_id] = ConsecutiveToolCallState()
        return self._states[session_id]
    
    async def check(
        self,
        event: AgentEvent,
        session_id: str,
    ) -> LoopDetectionResult:
        """Check for consecutive identical tool calls."""
        # Only process tool call events
        if event.type != AgentEventType.TOOL_CALL:
            return LoopDetectionResult()
        
        state = self._get_state(session_id)
        call_hash = event.get_tool_call_hash()
        
        if not call_hash:
            return LoopDetectionResult()
        
        if state.last_hash == call_hash:
            # Same call as last time
            state.repetition_count += 1
        else:
            # Different call, reset
            state.last_hash = call_hash
            state.repetition_count = 1
            state.last_tool_name = event.tool_name
            state.last_tool_args = event.tool_args
        
        # Check threshold
        if state.repetition_count >= self.threshold:
            import json
            args_preview = json.dumps(state.last_tool_args or {})[:100]
            
            return LoopDetectionResult(
                detected=True,
                loop_type=LoopType.CONSECUTIVE_TOOL_CALLS,
                confidence=min(1.0, 0.5 + (state.repetition_count - self.threshold) * 0.1),
                detail=f"Tool '{state.last_tool_name}' called {state.repetition_count} times with args: {args_preview}...",
                repetition_count=state.repetition_count,
            )
        
        return LoopDetectionResult()
    
    def reset_session(self, session_id: str) -> None:
        """Reset state for session."""
        self._states.pop(session_id, None)


@dataclass
class ContentRepetitionState:
    """State for tracking content repetition."""
    content_history: str = ""
    chunk_stats: Dict[str, List[int]] = field(default_factory=dict)  # hash -> indices
    last_index: int = 0
    in_code_block: bool = False


class ContentRepetitionDetector(LoopDetector):
    """
    Detects repetitive content patterns in streaming text.
    
    Uses chunk-based hashing to detect when the model is producing
    repetitive content (e.g., "chanting" the same phrases).
    
    Inspired by gemini-cli's content loop detection with CONTENT_CHUNK_SIZE
    and CONTENT_LOOP_THRESHOLD.
    """
    
    def __init__(
        self,
        threshold: int = 10,
        chunk_size: int = 50,
        max_history: int = 5000,
    ):
        """
        Args:
            threshold: Number of chunk repetitions to trigger detection
            chunk_size: Size of chunks for analysis
            max_history: Maximum content history to retain
        """
        self.threshold = threshold
        self.chunk_size = chunk_size
        self.max_history = max_history
        self._states: Dict[str, ContentRepetitionState] = {}
    
    def _get_state(self, session_id: str) -> ContentRepetitionState:
        """Get or create state for session."""
        if session_id not in self._states:
            self._states[session_id] = ContentRepetitionState()
        return self._states[session_id]
    
    async def check(
        self,
        event: AgentEvent,
        session_id: str,
    ) -> LoopDetectionResult:
        """Check for content repetition."""
        # Only process content events
        if event.type != AgentEventType.CONTENT or not event.content:
            # Reset on tool calls (content chanting only in single stream)
            if event.type == AgentEventType.TOOL_CALL:
                self._reset_content_tracking(session_id)
            return LoopDetectionResult()
        
        state = self._get_state(session_id)
        content = event.content
        
        # Skip code blocks (repetitive syntax is common)
        if self._should_skip_content(content, state):
            return LoopDetectionResult()
        
        # Add to history
        state.content_history += content
        
        # Truncate if too long
        self._truncate_history(state)
        
        # Analyze chunks
        return self._analyze_chunks(state)
    
    def _should_skip_content(self, content: str, state: ContentRepetitionState) -> bool:
        """Check if content should be skipped (code blocks, tables, etc.)."""
        num_fences = content.count("```")
        has_table = "|" in content and ("-" in content or "+" in content)
        has_list = any(content.strip().startswith(p) for p in ("- ", "* ", "+ ", "1. "))
        has_heading = content.strip().startswith("#")
        is_divider = set(content.strip()).issubset(set("-=_*+│─"))
        
        if num_fences or has_table or has_list or has_heading:
            self._reset_content_tracking_state(state)
            return True
        
        # Track code block state
        fence_count = content.count("```")
        was_in_code = state.in_code_block
        if fence_count % 2 == 1:
            state.in_code_block = not state.in_code_block
        
        if was_in_code or state.in_code_block or is_divider:
            return True
        
        return False
    
    def _truncate_history(self, state: ContentRepetitionState) -> None:
        """Truncate history and adjust indices."""
        if len(state.content_history) <= self.max_history:
            return
        
        truncation = len(state.content_history) - self.max_history
        state.content_history = state.content_history[truncation:]
        state.last_index = max(0, state.last_index - truncation)
        
        # Adjust chunk stats
        new_stats = {}
        for hash_val, indices in state.chunk_stats.items():
            adjusted = [i - truncation for i in indices if i >= truncation]
            if adjusted:
                new_stats[hash_val] = adjusted
        state.chunk_stats = new_stats
    
    def _analyze_chunks(self, state: ContentRepetitionState) -> LoopDetectionResult:
        """Analyze content chunks for repetition."""
        while state.last_index + self.chunk_size <= len(state.content_history):
            chunk = state.content_history[state.last_index:state.last_index + self.chunk_size]
            chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()
            
            existing = state.chunk_stats.get(chunk_hash)
            
            if not existing:
                state.chunk_stats[chunk_hash] = [state.last_index]
            else:
                # Verify actual content match (not just hash collision)
                first_idx = existing[0]
                original = state.content_history[first_idx:first_idx + self.chunk_size]
                
                if original == chunk:
                    existing.append(state.last_index)
                    
                    # Check if threshold reached
                    if len(existing) >= self.threshold:
                        # Verify clustered repetition
                        recent = existing[-self.threshold:]
                        total_distance = recent[-1] - recent[0]
                        avg_distance = total_distance / (self.threshold - 1)
                        max_allowed = self.chunk_size * 5
                        
                        if avg_distance <= max_allowed:
                            # Verify repeating sequence, not shared prefix
                            periods = set()
                            for i in range(len(recent) - 1):
                                period = state.content_history[recent[i]:recent[i + 1]]
                                periods.add(period)
                            
                            if len(periods) <= self.threshold // 2:
                                preview = chunk[:50]
                                return LoopDetectionResult(
                                    detected=True,
                                    loop_type=LoopType.CONTENT_REPETITION,
                                    confidence=min(1.0, 0.6 + len(existing) * 0.05),
                                    detail=f"Repeating content: '{preview}...'",
                                    repetition_count=len(existing),
                                )
            
            state.last_index += 1
        
        return LoopDetectionResult()
    
    def _reset_content_tracking(self, session_id: str) -> None:
        """Reset content tracking for session."""
        if session_id in self._states:
            self._reset_content_tracking_state(self._states[session_id])
    
    def _reset_content_tracking_state(self, state: ContentRepetitionState) -> None:
        """Reset content tracking state."""
        state.content_history = ""
        state.chunk_stats = {}
        state.last_index = 0
        # Preserve code block state
    
    def reset_session(self, session_id: str) -> None:
        """Reset state for session."""
        self._states.pop(session_id, None)


@dataclass
class StagnantStateTracker:
    """State for tracking stagnation."""
    turns_without_progress: int = 0
    last_tool_results: List[str] = field(default_factory=list)
    last_content_hashes: List[str] = field(default_factory=list)
    last_successful_action: Optional[str] = None


class StagnantStateDetector(LoopDetector):
    """
    Detects stagnant state where no progress is being made.
    
    Tracks whether recent turns have produced any meaningful change
    or progress. Triggers when N consecutive turns show no progress.
    """
    
    def __init__(self, threshold: int = 5):
        """
        Args:
            threshold: Turns without progress to trigger detection
        """
        self.threshold = threshold
        self._states: Dict[str, StagnantStateTracker] = {}
    
    def _get_state(self, session_id: str) -> StagnantStateTracker:
        """Get or create state for session."""
        if session_id not in self._states:
            self._states[session_id] = StagnantStateTracker()
        return self._states[session_id]
    
    async def check(
        self,
        event: AgentEvent,
        session_id: str,
    ) -> LoopDetectionResult:
        """Check for stagnant state."""
        state = self._get_state(session_id)
        
        # Track turn ends for progress evaluation
        if event.type == AgentEventType.TURN_END:
            # Check if any progress was made
            # (This is a simplified heuristic - in practice would check more signals)
            state.turns_without_progress += 1
            
            if state.turns_without_progress >= self.threshold:
                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.STAGNANT_STATE,
                    confidence=min(1.0, 0.5 + (state.turns_without_progress - self.threshold) * 0.1),
                    detail=f"No progress detected for {state.turns_without_progress} turns",
                    repetition_count=state.turns_without_progress,
                )
        
        # Tool results can indicate progress
        elif event.type == AgentEventType.TOOL_RESULT:
            if event.tool_success:
                # Successful tool = progress, reset counter
                state.turns_without_progress = 0
                state.last_successful_action = event.tool_name
            else:
                # Failed tool - track if same failure repeated
                result_hash = hashlib.md5((event.tool_result or "").encode()).hexdigest()
                if result_hash in state.last_tool_results[-3:]:
                    # Same failure repeated
                    state.turns_without_progress += 1
                else:
                    state.last_tool_results.append(result_hash)
                    state.last_tool_results = state.last_tool_results[-10:]  # Keep recent
        
        # Content that includes new information resets counter
        elif event.type == AgentEventType.CONTENT:
            content = event.content or ""
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            if content_hash not in state.last_content_hashes[-5:]:
                # New content = some progress
                state.turns_without_progress = max(0, state.turns_without_progress - 1)
                state.last_content_hashes.append(content_hash)
                state.last_content_hashes = state.last_content_hashes[-10:]
        
        return LoopDetectionResult()
    
    def reset_session(self, session_id: str) -> None:
        """Reset state for session."""
        self._states.pop(session_id, None)


class ToolResultSimilarityDetector(LoopDetector):
    """
    Detects loops via embedding similarity of tool results.
    
    Uses embedding vectors to detect when consecutive tool results
    are semantically identical, even if not exactly the same text.
    
    Requires an embedding provider to be configured.
    """
    
    def __init__(
        self,
        threshold: float = 0.95,
        window_size: int = 5,
        embedding_provider: Optional[Any] = None,
    ):
        """
        Args:
            threshold: Similarity threshold (0.0-1.0)
            window_size: Number of recent results to compare
            embedding_provider: Provider for computing embeddings
        """
        self.threshold = threshold
        self.window_size = window_size
        self.embedding_provider = embedding_provider
        self._states: Dict[str, List[Any]] = {}  # session_id -> recent embeddings
    
    async def check(
        self,
        event: AgentEvent,
        session_id: str,
    ) -> LoopDetectionResult:
        """Check for similar tool results."""
        # Only process tool results with embedding provider
        if (
            event.type != AgentEventType.TOOL_RESULT or
            not event.tool_result or
            not self.embedding_provider
        ):
            return LoopDetectionResult()
        
        # Get embeddings for current result
        try:
            embedding = await self.embedding_provider.embed(event.tool_result[:1000])
        except Exception:
            return LoopDetectionResult()
        
        # Get state
        if session_id not in self._states:
            self._states[session_id] = []
        recent = self._states[session_id]
        
        # Compare with recent embeddings
        similar_count = 0
        for prev_embedding in recent:
            similarity = self._cosine_similarity(embedding, prev_embedding)
            if similarity >= self.threshold:
                similar_count += 1
        
        # Add current embedding
        recent.append(embedding)
        if len(recent) > self.window_size:
            recent.pop(0)
        
        # Check if too many similar results
        if similar_count >= 3:
            return LoopDetectionResult(
                detected=True,
                loop_type=LoopType.TOOL_RESULT_SIMILARITY,
                confidence=min(1.0, 0.6 + similar_count * 0.1),
                detail=f"Tool results semantically similar {similar_count} times",
                repetition_count=similar_count,
            )
        
        return LoopDetectionResult()
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot_product / (norm_a * norm_b)
    
    def reset_session(self, session_id: str) -> None:
        """Reset state for session."""
        self._states.pop(session_id, None)
