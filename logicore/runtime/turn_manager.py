"""
TurnManager: Bounded execution with state machine.

Responsibilities:
- Enforce maximum turns per session
- Track turn state transitions (Pending → Active → Completed/Failed)
- Support nested execution tracking (for subagents)
- Dynamic budget adjustment
- Lifecycle hooks for telemetry

Inspired by gemini-cli's turn lifecycle management.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable, Awaitable
from contextlib import asynccontextmanager

from logicore.runtime.config import RuntimeConfig


class TurnStatus(Enum):
    """Status of a turn in the execution lifecycle."""
    PENDING = "pending"        # Turn created but not started
    ACTIVE = "active"          # Turn currently executing
    COMPLETED = "completed"    # Turn finished successfully
    FAILED = "failed"          # Turn finished with error
    CANCELLED = "cancelled"    # Turn was cancelled
    TIMEOUT = "timeout"        # Turn exceeded time limit


@dataclass
class TurnContext:
    """
    Context for a single turn in the agent execution.
    
    Captures all metadata needed for telemetry, debugging, and recovery.
    """
    turn_id: str
    session_id: str
    turn_number: int
    status: TurnStatus = TurnStatus.PENDING
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    
    # Execution metadata
    tool_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    
    # Parent tracking (for nested/subagent turns)
    parent_turn_id: Optional[str] = None
    depth: int = 0
    
    # Error information
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    # Recovery information
    recovery_attempts: int = 0
    recovery_actions: List[str] = field(default_factory=list)
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Get turn duration in milliseconds."""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() * 1000
        return None
    
    @property
    def is_terminal(self) -> bool:
        """Check if turn has reached a terminal state."""
        return self.status in (
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
            TurnStatus.TIMEOUT,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize turn context for logging/telemetry."""
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "turn_number": self.turn_number,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "tool_calls": self.tool_calls,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "parent_turn_id": self.parent_turn_id,
            "depth": self.depth,
            "error": self.error,
            "error_type": self.error_type,
            "recovery_attempts": self.recovery_attempts,
            "recovery_actions": self.recovery_actions,
        }


@dataclass
class SessionState:
    """State tracking for a session."""
    session_id: str
    turns_used: int = 0
    budget_adjustments: int = 0  # Dynamic budget changes
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    active_turn: Optional[TurnContext] = None
    turn_history: List[TurnContext] = field(default_factory=list)
    
    @property
    def total_budget(self) -> int:
        """Get effective budget including adjustments."""
        # Note: base budget comes from config, this is just adjustments
        return self.budget_adjustments


# Type alias for lifecycle hooks
TurnHook = Callable[[TurnContext], Awaitable[None]]


class TurnManager:
    """
    Manages bounded execution of agent turns with state tracking.
    
    Features:
    - Enforces max_turns limit per session
    - Tracks turn state transitions
    - Supports nested execution (subagents)
    - Dynamic budget adjustment
    - Lifecycle hooks for telemetry
    
    Usage:
        manager = TurnManager(config)
        
        async with manager.turn(session_id) as turn:
            # Execute agent logic
            turn.tool_calls += 1
            turn.tokens_input = 500
            turn.tokens_output = 200
        
        # Check remaining budget
        remaining = manager.get_remaining_turns(session_id)
    """
    
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._sessions: Dict[str, SessionState] = {}
        self._active_turns: Dict[str, TurnContext] = {}  # turn_id -> TurnContext
        
        # Lifecycle hooks
        self._on_turn_start: List[TurnHook] = []
        self._on_turn_end: List[TurnHook] = []
        self._on_budget_exceeded: List[TurnHook] = []
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    def register_on_turn_start(self, hook: TurnHook) -> None:
        """Register a hook to be called when a turn starts."""
        self._on_turn_start.append(hook)
    
    def register_on_turn_end(self, hook: TurnHook) -> None:
        """Register a hook to be called when a turn ends."""
        self._on_turn_end.append(hook)
    
    def register_on_budget_exceeded(self, hook: TurnHook) -> None:
        """Register a hook to be called when budget is exceeded."""
        self._on_budget_exceeded.append(hook)
    
    def _get_or_create_session(self, session_id: str) -> SessionState:
        """Get existing session or create new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]
    
    def get_remaining_turns(self, session_id: str) -> int:
        """Get remaining turn budget for a session."""
        session = self._get_or_create_session(session_id)
        effective_budget = self.config.max_turns + session.total_budget
        return max(0, effective_budget - session.turns_used)
    
    def is_budget_exceeded(self, session_id: str) -> bool:
        """Check if session has exceeded its turn budget."""
        return self.get_remaining_turns(session_id) <= 0
    
    def adjust_budget(self, session_id: str, delta: int) -> int:
        """
        Adjust the turn budget for a session.
        
        Args:
            session_id: Session to adjust
            delta: Positive to increase budget, negative to decrease
        
        Returns:
            New remaining turns count
        """
        session = self._get_or_create_session(session_id)
        session.budget_adjustments += delta
        return self.get_remaining_turns(session_id)
    
    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """Get statistics for a session."""
        session = self._get_or_create_session(session_id)
        
        total_tool_calls = sum(t.tool_calls for t in session.turn_history)
        total_tokens_in = sum(t.tokens_input for t in session.turn_history)
        total_tokens_out = sum(t.tokens_output for t in session.turn_history)
        
        completed = sum(1 for t in session.turn_history if t.status == TurnStatus.COMPLETED)
        failed = sum(1 for t in session.turn_history if t.status == TurnStatus.FAILED)
        
        durations = [t.duration_ms for t in session.turn_history if t.duration_ms]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        return {
            "session_id": session_id,
            "turns_used": session.turns_used,
            "turns_remaining": self.get_remaining_turns(session_id),
            "budget_adjustments": session.budget_adjustments,
            "total_tool_calls": total_tool_calls,
            "total_tokens_input": total_tokens_in,
            "total_tokens_output": total_tokens_out,
            "completed_turns": completed,
            "failed_turns": failed,
            "average_turn_duration_ms": avg_duration,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
        }
    
    async def start_turn(
        self,
        session_id: str,
        parent_turn_id: Optional[str] = None,
    ) -> TurnContext:
        """
        Start a new turn for a session.
        
        Args:
            session_id: Session identifier
            parent_turn_id: Optional parent turn (for nested/subagent execution)
        
        Returns:
            TurnContext for the new turn
        
        Raises:
            RuntimeError: If budget exceeded or another turn is active
        """
        async with self._lock:
            session = self._get_or_create_session(session_id)
            
            # Check budget
            if self.is_budget_exceeded(session_id):
                turn = TurnContext(
                    turn_id=str(uuid.uuid4()),
                    session_id=session_id,
                    turn_number=session.turns_used + 1,
                    status=TurnStatus.FAILED,
                    error="Turn budget exceeded",
                    error_type="BudgetExceeded",
                )
                # Fire budget exceeded hooks
                for hook in self._on_budget_exceeded:
                    try:
                        await hook(turn)
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Turn budget exceeded for session {session_id}. "
                    f"Used {session.turns_used}/{self.config.max_turns + session.total_budget} turns."
                )
            
            # Check for active turn (unless nested)
            if session.active_turn and not parent_turn_id:
                raise RuntimeError(
                    f"Session {session_id} already has an active turn: {session.active_turn.turn_id}"
                )
            
            # Calculate depth for nested turns
            depth = 0
            if parent_turn_id and parent_turn_id in self._active_turns:
                depth = self._active_turns[parent_turn_id].depth + 1
            
            # Create new turn
            turn = TurnContext(
                turn_id=str(uuid.uuid4()),
                session_id=session_id,
                turn_number=session.turns_used + 1,
                status=TurnStatus.ACTIVE,
                started_at=datetime.now(),
                parent_turn_id=parent_turn_id,
                depth=depth,
            )
            
            # Update session
            session.turns_used += 1
            session.last_activity = datetime.now()
            if not parent_turn_id:
                session.active_turn = turn
            
            # Track active turn
            self._active_turns[turn.turn_id] = turn
        
        # Fire start hooks (outside lock)
        for hook in self._on_turn_start:
            try:
                await hook(turn)
            except Exception:
                pass  # Don't let hook failures break execution
        
        return turn
    
    async def end_turn(
        self,
        turn_id: str,
        status: TurnStatus = TurnStatus.COMPLETED,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> TurnContext:
        """
        End a turn and update its status.
        
        Args:
            turn_id: Turn to end
            status: Final status
            error: Error message if failed
            error_type: Error type/category
        
        Returns:
            Updated TurnContext
        """
        async with self._lock:
            if turn_id not in self._active_turns:
                raise ValueError(f"Turn {turn_id} is not active")
            
            turn = self._active_turns[turn_id]
            turn.status = status
            turn.ended_at = datetime.now()
            turn.error = error
            turn.error_type = error_type
            
            # Update session
            session = self._get_or_create_session(turn.session_id)
            session.turn_history.append(turn)
            session.last_activity = datetime.now()
            
            # Clear active turn if this was the main turn
            if session.active_turn and session.active_turn.turn_id == turn_id:
                session.active_turn = None
            
            # Remove from active turns
            del self._active_turns[turn_id]
        
        # Fire end hooks (outside lock)
        for hook in self._on_turn_end:
            try:
                await hook(turn)
            except Exception:
                pass
        
        return turn
    
    @asynccontextmanager
    async def turn(
        self,
        session_id: str,
        parent_turn_id: Optional[str] = None,
    ):
        """
        Context manager for turn execution.
        
        Usage:
            async with manager.turn(session_id) as turn:
                # Execute agent logic
                turn.tool_calls += 1
        
        Automatically handles start/end and error handling.
        """
        turn = await self.start_turn(session_id, parent_turn_id)
        try:
            yield turn
            await self.end_turn(turn.turn_id, TurnStatus.COMPLETED)
        except asyncio.CancelledError:
            await self.end_turn(turn.turn_id, TurnStatus.CANCELLED)
            raise
        except asyncio.TimeoutError:
            await self.end_turn(
                turn.turn_id,
                TurnStatus.TIMEOUT,
                error="Turn timed out",
                error_type="Timeout",
            )
            raise
        except Exception as e:
            await self.end_turn(
                turn.turn_id,
                TurnStatus.FAILED,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise
    
    def get_active_turn(self, session_id: str) -> Optional[TurnContext]:
        """Get the currently active turn for a session."""
        session = self._sessions.get(session_id)
        return session.active_turn if session else None
    
    def get_turn_history(self, session_id: str) -> List[TurnContext]:
        """Get turn history for a session."""
        session = self._sessions.get(session_id)
        return list(session.turn_history) if session else []
    
    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            # Remove any active turns
            if session.active_turn:
                self._active_turns.pop(session.active_turn.turn_id, None)
            del self._sessions[session_id]
    
    def reset_session_budget(self, session_id: str) -> None:
        """Reset the turn counter for a session (e.g., for new conversation)."""
        session = self._get_or_create_session(session_id)
        session.turns_used = 0
        session.budget_adjustments = 0
        session.turn_history.clear()
