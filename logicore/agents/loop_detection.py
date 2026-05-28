"""
Loop Detection Integration

This module provides loop detection capabilities within the agents/ domain.
It bridges to the runtime implementation while maintaining architectural alignment.

Usage:
    from logicore.agents.loop_detection import (
        LoopDetector,
        LoopDetectionEngine,
        detect_tool_loop,
        detect_content_loop,
    )

Once runtime/ is deprecated, this becomes the authoritative module.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from logicore.agents.agent import Agent

# Re-export from runtime for compatibility
try:
    from logicore.runtime.loop_detection import (
        LoopDetectionEngine,
        LoopDetectionResult,
        LoopType,
        AgentEvent,
        AgentEventType,
        RecoveryAction,
        RecoveryActionType,
        ConsecutiveToolCallDetector,
        ContentRepetitionDetector,
        get_recovery_action,
    )
    _RUNTIME_AVAILABLE = True
except ImportError:
    _RUNTIME_AVAILABLE = False


# === Lightweight standalone implementation ===
# Used when runtime/ is not available or as primary after migration

class LoopType(Enum):
    """Types of loops that can be detected."""
    TOOL_CALL = "tool_call"
    CONTENT_REPETITION = "content_repetition"
    STAGNANT_STATE = "stagnant_state"


@dataclass
class LoopState:
    """Tracks loop detection state for a session."""
    session_id: str
    
    # Tool call tracking
    recent_tool_hashes: List[str] = field(default_factory=list)
    consecutive_identical: int = 0
    
    # Content tracking
    content_chunks: List[str] = field(default_factory=list)
    
    # Stagnation tracking
    last_progress_turn: int = 0
    current_turn: int = 0
    
    # Cooldowns: tool_name -> cooldown_until timestamp
    cooldowns: Dict[str, float] = field(default_factory=dict)
    
    # Detection disabled flag
    disabled: bool = False


class LoopDetector:
    """
    Lightweight loop detector that integrates with Agent execution.
    
    This is the preferred interface for loop detection within agents/
    architecture. It can delegate to runtime.LoopDetectionEngine or
    operate standalone.
    
    Usage:
        detector = LoopDetector(tool_threshold=5, content_threshold=10)
        
        # In agent execution loop:
        result = detector.check_tool_call(session_id, tool_name, tool_args)
        if result.detected:
            # Apply recovery action
            if result.should_cooldown:
                detector.apply_cooldown(session_id, tool_name)
    """
    
    def __init__(
        self,
        tool_threshold: int = 5,
        content_threshold: int = 10,
        content_chunk_size: int = 50,
        stagnant_threshold: int = 5,
        use_runtime_engine: bool = False,
    ):
        self.tool_threshold = tool_threshold
        self.content_threshold = content_threshold
        self.content_chunk_size = content_chunk_size
        self.stagnant_threshold = stagnant_threshold
        
        # Session state
        self._sessions: Dict[str, LoopState] = {}
        
        # Optionally delegate to runtime engine
        self._runtime_engine = None
        if use_runtime_engine and _RUNTIME_AVAILABLE:
            from logicore.runtime import RuntimeConfig
            self._runtime_engine = LoopDetectionEngine(RuntimeConfig())
    
    def _get_state(self, session_id: str) -> LoopState:
        """Get or create session state."""
        if session_id not in self._sessions:
            self._sessions[session_id] = LoopState(session_id=session_id)
        return self._sessions[session_id]
    
    def check_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> "LoopCheckResult":
        """
        Check if a tool call indicates a loop.
        
        Returns LoopCheckResult with detection status and suggested action.
        """
        state = self._get_state(session_id)
        
        if state.disabled:
            return LoopCheckResult(detected=False)
        
        # Check cooldown
        if self.is_tool_cooled_down(session_id, tool_name):
            return LoopCheckResult(
                detected=True,
                loop_type=LoopType.TOOL_CALL,
                message=f"Tool '{tool_name}' is in cooldown",
                should_skip=True,
            )
        
        # Hash the tool call
        import json
        call_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
        call_hash = hashlib.sha256(call_sig.encode()).hexdigest()[:16]
        
        # Check for consecutive identical calls
        if state.recent_tool_hashes and state.recent_tool_hashes[-1] == call_hash:
            state.consecutive_identical += 1
        else:
            state.consecutive_identical = 1
        
        state.recent_tool_hashes.append(call_hash)
        
        # Trim history
        if len(state.recent_tool_hashes) > 20:
            state.recent_tool_hashes = state.recent_tool_hashes[-20:]
        
        # Check threshold
        if state.consecutive_identical >= self.tool_threshold:
            return LoopCheckResult(
                detected=True,
                loop_type=LoopType.TOOL_CALL,
                message=f"Tool '{tool_name}' called {state.consecutive_identical} times consecutively",
                repetition_count=state.consecutive_identical,
                should_cooldown=True,
                tool_name=tool_name,
            )
        
        return LoopCheckResult(detected=False)
    
    def check_content(self, session_id: str, content: str) -> "LoopCheckResult":
        """Check if content indicates a repetition loop."""
        state = self._get_state(session_id)
        
        if state.disabled or not content:
            return LoopCheckResult(detected=False)
        
        # Extract chunks
        chunks = [
            content[i:i + self.content_chunk_size]
            for i in range(0, len(content), self.content_chunk_size)
        ]
        
        # Count repetitions
        repetitions = 0
        for chunk in chunks:
            if chunk in state.content_chunks:
                repetitions += 1
        
        state.content_chunks.extend(chunks)
        
        # Trim history
        if len(state.content_chunks) > 100:
            state.content_chunks = state.content_chunks[-100:]
        
        if repetitions >= self.content_threshold:
            return LoopCheckResult(
                detected=True,
                loop_type=LoopType.CONTENT_REPETITION,
                message=f"Content repetition detected ({repetitions} repeated chunks)",
                repetition_count=repetitions,
            )
        
        return LoopCheckResult(detected=False)
    
    def check_stagnation(self, session_id: str, made_progress: bool) -> "LoopCheckResult":
        """Check if agent is stagnating (no progress)."""
        state = self._get_state(session_id)
        state.current_turn += 1
        
        if made_progress:
            state.last_progress_turn = state.current_turn
            return LoopCheckResult(detected=False)
        
        turns_without_progress = state.current_turn - state.last_progress_turn
        
        if turns_without_progress >= self.stagnant_threshold:
            return LoopCheckResult(
                detected=True,
                loop_type=LoopType.STAGNANT_STATE,
                message=f"No progress for {turns_without_progress} turns",
                repetition_count=turns_without_progress,
            )
        
        return LoopCheckResult(detected=False)
    
    def is_tool_cooled_down(self, session_id: str, tool_name: str) -> bool:
        """Check if a tool is in cooldown."""
        state = self._get_state(session_id)
        cooldown_until = state.cooldowns.get(tool_name, 0)
        return time.time() < cooldown_until
    
    def apply_cooldown(
        self,
        session_id: str,
        tool_name: str,
        duration_seconds: int = 60,
    ) -> None:
        """Apply cooldown to a tool."""
        state = self._get_state(session_id)
        state.cooldowns[tool_name] = time.time() + duration_seconds
    
    def clear_cooldown(self, session_id: str, tool_name: str) -> None:
        """Clear cooldown for a tool."""
        state = self._get_state(session_id)
        state.cooldowns.pop(tool_name, None)
    
    def disable(self, session_id: str) -> None:
        """Disable loop detection for a session."""
        self._get_state(session_id).disabled = True
    
    def enable(self, session_id: str) -> None:
        """Re-enable loop detection for a session."""
        self._get_state(session_id).disabled = False
    
    def reset(self, session_id: str) -> None:
        """Reset all state for a session."""
        self._sessions.pop(session_id, None)


@dataclass
class LoopCheckResult:
    """Result of a loop detection check."""
    detected: bool
    loop_type: Optional[LoopType] = None
    message: Optional[str] = None
    repetition_count: int = 0
    should_cooldown: bool = False
    should_skip: bool = False
    tool_name: Optional[str] = None
    
    def get_recovery_message(self) -> Optional[str]:
        """Get a message to inject for recovery."""
        if not self.detected:
            return None
        
        if self.loop_type == LoopType.TOOL_CALL:
            return (
                f"I notice I've been repeating the same action. "
                f"Let me try a different approach to solve this problem."
            )
        elif self.loop_type == LoopType.CONTENT_REPETITION:
            return (
                f"I seem to be repeating myself. "
                f"Let me reconsider the problem and try something new."
            )
        elif self.loop_type == LoopType.STAGNANT_STATE:
            return (
                f"I haven't made progress recently. "
                f"Let me step back and think about this differently."
            )
        
        return None


# === Convenience functions ===

def detect_tool_loop(
    session_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    threshold: int = 5,
) -> LoopCheckResult:
    """Quick check for tool call loops."""
    detector = LoopDetector(tool_threshold=threshold)
    return detector.check_tool_call(session_id, tool_name, tool_args)


def detect_content_loop(
    session_id: str,
    content: str,
    threshold: int = 10,
) -> LoopCheckResult:
    """Quick check for content repetition loops."""
    detector = LoopDetector(content_threshold=threshold)
    return detector.check_content(session_id, content)


# === Exports ===

__all__ = [
    # Core classes
    "LoopDetector",
    "LoopCheckResult",
    "LoopState",
    "LoopType",
    # Convenience functions
    "detect_tool_loop",
    "detect_content_loop",
]

# Add runtime exports if available
if _RUNTIME_AVAILABLE:
    __all__.extend([
        "LoopDetectionEngine",
        "LoopDetectionResult",
        "AgentEvent",
        "AgentEventType",
        "RecoveryAction",
        "RecoveryActionType",
        "get_recovery_action",
    ])
