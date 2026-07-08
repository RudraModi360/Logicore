"""
TelemetryCollector: Comprehensive observability for agent execution.

Collects and aggregates:
- Loop detection events
- Turn lifecycle metrics
- Token usage statistics
- Tool execution analytics
- Context management events
- Recovery action tracking

Supports JSON export and future OpenTelemetry integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable

from logicore.runtime.config import RuntimeConfig


class TelemetryEventType(Enum):
    """Types of telemetry events."""
    # Turn events
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TURN_TIMEOUT = "turn_timeout"
    
    # Loop events
    LOOP_DETECTED = "loop_detected"
    LOOP_RECOVERY = "loop_recovery"
    LOOP_DISABLED = "loop_disabled"
    
    # Tool events
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_ERROR = "tool_call_error"
    TOOL_COOLDOWN = "tool_cooldown"
    TOOL_DEDUPLICATED = "tool_deduplicated"
    
    # Context events
    CONTEXT_COMPRESSED = "context_compressed"
    CONTEXT_MASKED = "context_masked"
    CONTEXT_TRUNCATED = "context_truncated"
    
    # Token events
    TOKEN_BUDGET_WARNING = "token_budget_warning"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    
    # Recovery events
    RECOVERY_ACTION = "recovery_action"
    RECOVERY_ESCALATION = "recovery_escalation"


@dataclass
class TelemetryEvent:
    """A single telemetry event."""
    type: TelemetryEventType
    timestamp: datetime = field(default_factory=datetime.now)
    session_id: str = "default"
    turn_id: Optional[str] = None
    
    # Event-specific data
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Performance metrics
    duration_ms: Optional[float] = None
    tokens_used: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/export."""
        return {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "data": self.data,
            "duration_ms": self.duration_ms,
            "tokens_used": self.tokens_used,
        }
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())


@dataclass
class SessionMetrics:
    """Aggregated runtime execution metrics for a session (turns, tools, loops, context)."""
    session_id: str
    
    # Turn metrics
    turns_total: int = 0
    turns_completed: int = 0
    turns_failed: int = 0
    
    # Tool metrics
    tool_calls_total: int = 0
    tool_calls_success: int = 0
    tool_calls_error: int = 0
    tool_calls_deduplicated: int = 0
    
    # Loop metrics
    loops_detected: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    
    # Context metrics
    compressions: int = 0
    tokens_compressed: int = 0
    maskings: int = 0
    tokens_masked: int = 0
    
    # Token metrics
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    
    # Timing
    total_duration_ms: float = 0.0
    avg_turn_duration_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for export."""
        return {
            "session_id": self.session_id,
            "turns": {
                "total": self.turns_total,
                "completed": self.turns_completed,
                "failed": self.turns_failed,
            },
            "tool_calls": {
                "total": self.tool_calls_total,
                "success": self.tool_calls_success,
                "error": self.tool_calls_error,
                "deduplicated": self.tool_calls_deduplicated,
            },
            "loops": {
                "detected": self.loops_detected,
                "recovery_attempts": self.recovery_attempts,
                "recovery_successes": self.recovery_successes,
            },
            "context": {
                "compressions": self.compressions,
                "tokens_compressed": self.tokens_compressed,
                "maskings": self.maskings,
                "tokens_masked": self.tokens_masked,
            },
            "tokens": {
                "total_input": self.total_tokens_input,
                "total_output": self.total_tokens_output,
            },
            "timing": {
                "total_duration_ms": self.total_duration_ms,
                "avg_turn_duration_ms": self.avg_turn_duration_ms,
            },
        }


@dataclass
class LoopStatistics:
    """Statistics about loop detection."""
    total_detected: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    recovery_success_rate: float = 0.0
    avg_recovery_attempts: float = 0.0
    cooldowns_applied: int = 0
    sessions_disabled: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for export."""
        return {
            "total_detected": self.total_detected,
            "by_type": self.by_type,
            "recovery_success_rate": self.recovery_success_rate,
            "avg_recovery_attempts": self.avg_recovery_attempts,
            "cooldowns_applied": self.cooldowns_applied,
            "sessions_disabled": self.sessions_disabled,
        }


class TelemetryCollector:
    """
    Collects and aggregates telemetry from the agent runtime.
    
    Usage:
        collector = TelemetryCollector(config)
        
        # Record events
        collector.record_event(TelemetryEvent(
            type=TelemetryEventType.TURN_START,
            session_id="abc123",
            turn_id="turn_1",
        ))
        
        # Get metrics
        metrics = collector.get_session_metrics("abc123")
        
        # Export
        json_data = collector.export_json()
    """
    
    def __init__(self, config: RuntimeConfig):
        """
        Args:
            config: Runtime configuration
        """
        self.config = config
        
        # Event storage
        self._events: List[TelemetryEvent] = []
        self._max_events = 10000
        
        # Session metrics
        self._session_metrics: Dict[str, SessionMetrics] = {}
        
        # Loop statistics
        self._loop_stats = LoopStatistics()
        
        # Event callbacks
        self._callbacks: List[Callable[[TelemetryEvent], None]] = []
    
    def register_callback(self, callback: Callable[[TelemetryEvent], None]) -> None:
        """Register callback for new events."""
        self._callbacks.append(callback)
    
    def record_event(self, event: TelemetryEvent) -> None:
        """Record a telemetry event."""
        if not self.config.telemetry.enabled:
            return
        
        # Store event
        self._events.append(event)
        
        # Trim if needed
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        
        # Update metrics
        self._update_metrics(event)
        
        # Fire callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:
                pass
    
    def _update_metrics(self, event: TelemetryEvent) -> None:
        """Update aggregated metrics from event."""
        session_id = event.session_id
        
        # Get or create session metrics
        if session_id not in self._session_metrics:
            self._session_metrics[session_id] = SessionMetrics(session_id=session_id)
        metrics = self._session_metrics[session_id]
        
        # Update based on event type
        if event.type == TelemetryEventType.TURN_START:
            metrics.turns_total += 1
            
        elif event.type == TelemetryEventType.TURN_END:
            success = event.data.get("success", True)
            if success:
                metrics.turns_completed += 1
            else:
                metrics.turns_failed += 1
            
            if event.duration_ms:
                metrics.total_duration_ms += event.duration_ms
                if metrics.turns_total > 0:
                    metrics.avg_turn_duration_ms = metrics.total_duration_ms / metrics.turns_total
        
        elif event.type == TelemetryEventType.TOOL_CALL_START:
            metrics.tool_calls_total += 1
            
        elif event.type == TelemetryEventType.TOOL_CALL_END:
            success = event.data.get("success", True)
            if success:
                metrics.tool_calls_success += 1
            else:
                metrics.tool_calls_error += 1
        
        elif event.type == TelemetryEventType.TOOL_DEDUPLICATED:
            metrics.tool_calls_deduplicated += 1
        
        elif event.type == TelemetryEventType.LOOP_DETECTED:
            metrics.loops_detected += 1
            self._loop_stats.total_detected += 1
            
            loop_type = event.data.get("loop_type", "unknown")
            self._loop_stats.by_type[loop_type] = self._loop_stats.by_type.get(loop_type, 0) + 1
        
        elif event.type == TelemetryEventType.LOOP_RECOVERY:
            metrics.recovery_attempts += 1
            if event.data.get("success", False):
                metrics.recovery_successes += 1
        
        elif event.type == TelemetryEventType.CONTEXT_COMPRESSED:
            metrics.compressions += 1
            metrics.tokens_compressed += event.data.get("tokens_saved", 0)
        
        elif event.type == TelemetryEventType.CONTEXT_MASKED:
            metrics.maskings += 1
            metrics.tokens_masked += event.data.get("tokens_saved", 0)
        
        # Update token counts
        if event.tokens_used:
            if "input" in event.data:
                metrics.total_tokens_input += event.data.get("input", 0)
            if "output" in event.data:
                metrics.total_tokens_output += event.data.get("output", 0)
    
    def get_session_metrics(self, session_id: str) -> SessionMetrics:
        """Get aggregated metrics for a session."""
        return self._session_metrics.get(
            session_id,
            SessionMetrics(session_id=session_id),
        )
    
    def get_loop_statistics(self) -> LoopStatistics:
        """Get loop detection statistics."""
        # Calculate derived stats
        if self._loop_stats.total_detected > 0:
            total_attempts = sum(m.recovery_attempts for m in self._session_metrics.values())
            total_successes = sum(m.recovery_successes for m in self._session_metrics.values())
            
            if total_attempts > 0:
                self._loop_stats.recovery_success_rate = total_successes / total_attempts
                self._loop_stats.avg_recovery_attempts = total_attempts / self._loop_stats.total_detected
        
        return self._loop_stats
    
    def get_recent_events(
        self,
        count: int = 100,
        event_types: Optional[List[TelemetryEventType]] = None,
        session_id: Optional[str] = None,
    ) -> List[TelemetryEvent]:
        """Get recent events with optional filtering."""
        events = self._events
        
        if event_types:
            events = [e for e in events if e.type in event_types]
        
        if session_id:
            events = [e for e in events if e.session_id == session_id]
        
        return events[-count:]
    
    def export_json(self, session_id: Optional[str] = None) -> str:
        """Export telemetry data as JSON."""
        data = {
            "exported_at": datetime.now().isoformat(),
            "config": {
                "enabled": self.config.telemetry.enabled,
                "log_prompts": self.config.telemetry.log_prompts,
            },
            "loop_statistics": self.get_loop_statistics().to_dict(),
        }
        
        if session_id:
            data["session_metrics"] = self.get_session_metrics(session_id).to_dict()
            data["events"] = [e.to_dict() for e in self.get_recent_events(session_id=session_id)]
        else:
            data["session_metrics"] = {
                sid: metrics.to_dict()
                for sid, metrics in self._session_metrics.items()
            }
            data["events"] = [e.to_dict() for e in self.get_recent_events()]
        
        return json.dumps(data, indent=2)
    
    def clear_session(self, session_id: str) -> None:
        """Clear telemetry for a session."""
        self._session_metrics.pop(session_id, None)
        self._events = [e for e in self._events if e.session_id != session_id]
    
    def clear_all(self) -> None:
        """Clear all telemetry data."""
        self._events.clear()
        self._session_metrics.clear()
        self._loop_stats = LoopStatistics()
