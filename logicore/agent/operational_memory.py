"""Operational memory manager for failure pattern tracking and lessons learned.

Modeled after hermes-agent's cross-session learning patterns:
- Track failure patterns across sessions
- Extract lessons learned from errors
- Provide failure context to LLM for better recovery
- Store operational memories for future reference

This module is dependency-free so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from logicore.memory.types import (
    MemoryDomain,
    MemoryKind,
    MemoryStability,
    MemoryMetadata,
)

logger = logging.getLogger(__name__)


@dataclass
class FailurePattern:
    """A detected failure pattern from tool execution."""
    
    pattern_id: str
    error_type: str
    tool_name: Optional[str] = None
    error_message: str = ""
    recovery_action: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    last_seen: Optional[str] = None
    first_seen: Optional[str] = None
    
    def __post_init__(self):
        now = datetime.now().isoformat()
        if self.last_seen is None:
            self.last_seen = now
        if self.first_seen is None:
            self.first_seen = now
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total
    
    @property
    def failure_rate(self) -> float:
        """Calculate failure rate."""
        return 1.0 - self.success_rate


@dataclass
class OperationalLesson:
    """A lesson learned from operational experience."""
    
    lesson_id: str
    trigger: str  # What triggered this lesson (error type, tool, etc.)
    lesson: str   # The lesson learned
    confidence: float = 0.7
    examples: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    last_applied: Optional[str] = None
    application_count: int = 0
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


@dataclass
class OperationalMemoryState:
    """State tracking for operational memory within a session."""
    
    failure_patterns: Dict[str, FailurePattern] = field(default_factory=dict)
    lessons_applied: List[str] = field(default_factory=list)
    lessons_learned: List[OperationalLesson] = field(default_factory=list)
    recovery_attempts: Dict[str, int] = field(default_factory=dict)
    
    def record_failure(
        self,
        error_type: str,
        tool_name: Optional[str] = None,
        error_message: str = "",
    ) -> FailurePattern:
        """Record a failure and update pattern tracking."""
        # Create pattern ID
        pattern_id = f"{error_type}:{tool_name or 'unknown'}"
        
        if pattern_id in self.failure_patterns:
            pattern = self.failure_patterns[pattern_id]
            pattern.failure_count += 1
            pattern.last_seen = datetime.now().isoformat()
            pattern.error_message = error_message[:200]
        else:
            pattern = FailurePattern(
                pattern_id=pattern_id,
                error_type=error_type,
                tool_name=tool_name,
                error_message=error_message[:200],
                failure_count=1,
            )
            self.failure_patterns[pattern_id] = pattern
        
        return pattern
    
    def record_success(
        self,
        error_type: str,
        tool_name: Optional[str] = None,
    ):
        """Record a successful recovery."""
        pattern_id = f"{error_type}:{tool_name or 'unknown'}"
        
        if pattern_id in self.failure_patterns:
            pattern = self.failure_patterns[pattern_id]
            pattern.success_count += 1
            pattern.last_seen = datetime.now().isoformat()
    
    def record_recovery_attempt(self, error_type: str, tool_name: Optional[str] = None):
        """Record a recovery attempt."""
        key = f"{error_type}:{tool_name or 'unknown'}"
        self.recovery_attempts[key] = self.recovery_attempts.get(key, 0) + 1
    
    def get_recovery_count(self, error_type: str, tool_name: Optional[str] = None) -> int:
        """Get number of recovery attempts for this error."""
        key = f"{error_type}:{tool_name or 'unknown'}"
        return self.recovery_attempts.get(key, 0)
    
    def add_lesson(self, lesson: OperationalLesson):
        """Add a learned lesson."""
        self.lessons_learned.append(lesson)
    
    def apply_lesson(self, lesson_id: str):
        """Mark a lesson as applied."""
        if lesson_id not in self.lessons_applied:
            self.lessons_applied.append(lesson_id)
        
        # Update lesson stats
        for lesson in self.lessons_learned:
            if lesson.lesson_id == lesson_id:
                lesson.application_count += 1
                lesson.last_applied = datetime.now().isoformat()
                break


class OperationalMemoryManager:
    """Manages operational memory for failure patterns and lessons learned.
    
    This manager:
    1. Tracks failure patterns within a session
    2. Extracts lessons from repeated failures
    3. Provides failure context to LLM for better recovery
    4. Persists operational memories across sessions
    """
    
    def __init__(self, memory_store=None, debug: bool = False):
        """Initialize the operational memory manager.
        
        Args:
            memory_store: Optional MemoryStore for persistence
            debug: Enable debug logging
        """
        self.memory_store = memory_store
        self.debug = debug
        self._session_state = OperationalMemoryState()
    
    def record_tool_failure(
        self,
        tool_name: str,
        error_type: str,
        error_message: str,
        recovery_action: Optional[str] = None,
    ) -> FailurePattern:
        """Record a tool failure and update pattern tracking."""
        pattern = self._session_state.record_failure(
            error_type=error_type,
            tool_name=tool_name,
            error_message=error_message,
        )
        
        if recovery_action:
            pattern.recovery_action = recovery_action
        
        if self.debug:
            logger.debug(
                f"[OperationalMemory] Recorded failure: {pattern.pattern_id} "
                f"(failures={pattern.failure_count}, success={pattern.success_count})"
            )
        
        return pattern
    
    def record_tool_success(self, tool_name: str, error_type: str):
        """Record a successful tool execution after failure."""
        self._session_state.record_success(
            error_type=error_type,
            tool_name=tool_name,
        )
    
    def should_escalate_recovery(
        self,
        error_type: str,
        tool_name: Optional[str] = None,
        max_attempts: int = 3,
    ) -> bool:
        """Check if recovery should be escalated (too many attempts)."""
        count = self._session_state.get_recovery_count(error_type, tool_name)
        return count >= max_attempts
    
    def get_failure_context(self, tool_name: Optional[str] = None) -> str:
        """Get failure context for injection into LLM conversation.
        
        This provides the LLM with information about recent failures
        and patterns, helping it make better recovery decisions.
        """
        patterns = list(self._session_state.failure_patterns.values())
        
        if not patterns:
            return ""
        
        # Filter to relevant patterns if tool_name specified
        if tool_name:
            patterns = [p for p in patterns if p.tool_name == tool_name]
        
        if not patterns:
            return ""
        
        # Build context message
        lines = ["## Recent Failure Patterns"]
        for pattern in patterns[:5]:  # Limit to 5 most recent
            lines.append(
                f"- **{pattern.error_type}** on `{pattern.tool_name or 'unknown'}`: "
                f"Failed {pattern.failure_count} time(s), "
                f"succeeded {pattern.success_count} time(s). "
                f"Last error: {pattern.error_message[:100]}"
            )
        
        # Add lessons if available
        lessons = self._session_state.lessons_learned
        if lessons:
            lines.append("\n## Lessons Learned")
            for lesson in lessons[:3]:
                lines.append(f"- {lesson.lesson}")
        
        return "\n".join(lines)
    
    def extract_lesson(
        self,
        trigger: str,
        lesson: str,
        confidence: float = 0.7,
        examples: Optional[List[str]] = None,
    ) -> OperationalLesson:
        """Extract and store a lesson learned."""
        lesson_id = f"lesson_{len(self._session_state.lessons_learned) + 1}"
        
        operational_lesson = OperationalLesson(
            lesson_id=lesson_id,
            trigger=trigger,
            lesson=lesson,
            confidence=confidence,
            examples=examples or [],
        )
        
        self._session_state.add_lesson(operational_lesson)
        
        if self.debug:
            logger.debug(
                f"[OperationalMemory] Extracted lesson: {lesson_id} "
                f"(trigger={trigger}, confidence={confidence})"
            )
        
        return operational_lesson
    
    def format_for_system_prompt(self) -> str:
        """Format operational memory for inclusion in system prompt."""
        context = self.get_failure_context()
        if not context:
            return ""
        
        return (
            "## Operational Context (This Session)\n"
            "The following failure patterns and lessons have been observed "
            "in this session. Use this information to make better recovery decisions:\n\n"
            f"{context}"
        )
    
    def get_session_summary(self) -> Dict[str, Any]:
        """Get a summary of operational memory for this session."""
        return {
            "failure_patterns": len(self._session_state.failure_patterns),
            "lessons_learned": len(self._session_state.lessons_learned),
            "lessons_applied": len(self._session_state.lessons_applied),
            "recovery_attempts": sum(self._session_state.recovery_attempts.values()),
        }
    
    def reset_for_new_session(self):
        """Reset state for a new session."""
        self._session_state = OperationalMemoryState()
