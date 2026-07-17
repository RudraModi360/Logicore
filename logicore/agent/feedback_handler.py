"""Feedback handler for injecting correction hints into conversations.

Modeled after Claude Code's ``MEMORY_CORRECTION_HINT`` pattern:
- When a tool is cancelled/denied, inject hint about user corrections
- When user provides correction, inject hint for LLM to reason about it
- Track corrections in session state for learning

This module is dependency-free so it can be unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime

from logicore.agent.feedback_detector import (
    FeedbackDetector,
    FeedbackDetectionResult,
    DetectedCorrection,
    CorrectionType,
)


# Correction hint messages (modeled after Claude Code's MEMORY_CORRECTION_HINT)
CORRECTION_HINT_TEMPLATE = (
    "Note: The user's next message may contain a correction or preference. "
    "Pay close attention — if they explain what went wrong, consider "
    "saving that to memory for future reference."
)

TOOL_DENIED_HINT = (
    "Note: The tool execution was denied or cancelled. "
    "The user may provide a correction or alternative approach in their next message. "
    "Pay close attention to what they say and adjust your behavior accordingly."
)

TOOL_FAILED_HINT = (
    "Note: The tool execution failed. "
    "The user may provide guidance on how to fix the issue. "
    "Listen carefully to their feedback and try a different approach."
)


@dataclass
class FeedbackInjection:
    """A feedback hint injected into the conversation."""
    
    hint_type: str  # "correction", "tool_denied", "tool_failed"
    message: str
    correction: Optional[DetectedCorrection] = None
    timestamp: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


@dataclass
class FeedbackHandlingResult:
    """Result of feedback handling."""
    
    injected_hints: List[FeedbackInjection] = field(default_factory=list)
    corrections_tracked: List[DetectedCorrection] = field(default_factory=list)
    should_continue: bool = True  # Whether to continue normal flow
    
    @property
    def has_injections(self) -> bool:
        return len(self.injected_hints) > 0
    
    @property
    def has_corrections(self) -> bool:
        return len(self.corrections_tracked) > 0


class FeedbackHandler:
    """Handles user feedback and correction injection.
    
    Detects corrections, injects hints into the conversation,
    and tracks corrections for learning.
    """
    
    def __init__(self, min_confidence: float = 0.6):
        """Initialize handler.
        
        Args:
            min_confidence: Minimum confidence threshold for detection
        """
        self.detector = FeedbackDetector(min_confidence=min_confidence)
        self._previous_agent_action: Optional[str] = None
    
    def set_previous_action(self, action: str):
        """Set the previous agent action for context."""
        self._previous_agent_action = action
    
    async def handle_user_message(
        self,
        user_message: str,
        session: Any,
        llm_call: Optional[Any] = None,
    ) -> FeedbackHandlingResult:
        """Handle a user message for feedback detection.
        
        Args:
            user_message: The user's message
            session: The session object for tracking
            llm_call: Optional async callable for LLM-based correction detection
            
        Returns:
            FeedbackHandlingResult with injected hints and tracked corrections
        """
        result = FeedbackHandlingResult()
        
        # Detect corrections (with optional LLM fallback)
        detection = await self.detector.detect_with_llm_fallback(
            user_message,
            previous_agent_action=self._previous_agent_action,
            llm_call=llm_call,
        )
        
        if detection.has_correction:
            # Track corrections in session
            for correction in detection.corrections:
                session.add_correction(
                    correction_type=correction.correction_type.value,
                    original=correction.original,
                    corrected=correction.corrected,
                    context=correction.context,
                )
                result.corrections_tracked.append(correction)
            
            # Inject hint if needed
            if detection.should_inject_hint and detection.hint_message:
                injection = FeedbackInjection(
                    hint_type="correction",
                    message=detection.hint_message,
                    correction=detection.primary_correction,
                )
                result.injected_hints.append(injection)
        
        # Clear previous action after processing
        self._previous_agent_action = None
        
        return result
    
    def handle_tool_denied(
        self,
        tool_name: str,
        session: Any,
    ) -> FeedbackHandlingResult:
        """Handle a tool denial (user cancelled or denied tool execution).
        
        Args:
            tool_name: Name of the denied tool
            session: The session object for tracking
            
        Returns:
            FeedbackHandlingResult with injected hints
        """
        result = FeedbackHandlingResult()
        
        # Inject tool denied hint
        injection = FeedbackInjection(
            hint_type="tool_denied",
            message=f"{TOOL_DENIED_HINT}\n\nTool that was denied: `{tool_name}`",
        )
        result.injected_hints.append(injection)
        
        return result
    
    def handle_tool_failed(
        self,
        tool_name: str,
        error: str,
        session: Any,
    ) -> FeedbackHandlingResult:
        """Handle a tool failure (tool execution failed).
        
        Args:
            tool_name: Name of the failed tool
            error: Error message from the tool
            session: The session object for tracking
            
        Returns:
            FeedbackHandlingResult with injected hints
        """
        result = FeedbackHandlingResult()
        
        # Inject tool failed hint
        injection = FeedbackInjection(
            hint_type="tool_failed",
            message=(
                f"{TOOL_FAILED_HINT}\n\n"
                f"Tool that failed: `{tool_name}`\n"
                f"Error: {error[:200]}"
            ),
        )
        result.injected_hints.append(injection)
        
        return result
    
    def get_session_corrections_summary(self, session: Any) -> str:
        """Get a summary of corrections made in this session."""
        corrections = getattr(session, 'corrections_made', [])
        
        if not corrections:
            return "No corrections recorded in this session."
        
        summaries = []
        for c in corrections[-5:]:  # Last 5 corrections
            summaries.append(
                f"- [{c.get('type', 'unknown')}] {c.get('corrected', '')[:100]}"
            )
        
        return (
            f"Session has {len(corrections)} correction(s). "
            f"Recent corrections:\n" + "\n".join(summaries)
        )
    
    def format_corrections_for_prompt(self, session: Any) -> str:
        """Format session corrections for inclusion in system prompt."""
        corrections = getattr(session, 'corrections_made', [])
        
        if not corrections:
            return ""
        
        # Group by type
        by_type: Dict[str, List[Dict]] = {}
        for c in corrections:
            ctype = c.get('type', 'other')
            if ctype not in by_type:
                by_type[ctype] = []
            by_type[ctype].append(c)
        
        lines = ["## User Corrections This Session"]
        for ctype, items in by_type.items():
            lines.append(f"\n### {ctype.replace('_', ' ').title()}")
            for item in items[-3:]:  # Last 3 of each type
                lines.append(f"- {item.get('corrected', '')[:100]}")
        
        return "\n".join(lines)
