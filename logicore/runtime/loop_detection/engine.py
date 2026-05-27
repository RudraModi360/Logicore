"""
LoopDetectionEngine: Multi-layer loop detection with pluggable detectors.

Architecture inspired by gemini-cli's loopDetectionService:
- Multiple detection strategies with weighted scoring
- Streaming-safe (processes events incrementally)
- Session-level disable capability
- Telemetry hooks for all detection events
- Recovery action recommendations

Detection Layers:
1. Hash-based: Identical tool calls (fast, exact)
2. Content-based: Repeated output chunks (streaming-safe)
3. Semantic: LLM-based conversation analysis (expensive, thorough)
4. Stagnant: No progress detection (state-based)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable, Awaitable, Set

from logicore.runtime.config import RuntimeConfig, RecoveryEscalationLevel


class LoopType(Enum):
    """Types of detected loops."""
    CONSECUTIVE_TOOL_CALLS = "consecutive_tool_calls"
    CONTENT_REPETITION = "content_repetition"
    SEMANTIC_LOOP = "semantic_loop"
    STAGNANT_STATE = "stagnant_state"
    TOOL_RESULT_SIMILARITY = "tool_result_similarity"


class AgentEventType(Enum):
    """Types of agent events for loop detection."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTENT = "content"
    TURN_START = "turn_start"
    TURN_END = "turn_end"


@dataclass
class AgentEvent:
    """Event from agent execution for loop detection analysis."""
    type: AgentEventType
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Tool call event data
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None
    tool_success: Optional[bool] = None
    
    # Content event data
    content: Optional[str] = None
    
    # Turn event data
    turn_id: Optional[str] = None
    turn_number: Optional[int] = None
    
    def get_tool_call_hash(self) -> Optional[str]:
        """Get hash of tool call for deduplication."""
        if self.type != AgentEventType.TOOL_CALL or not self.tool_name:
            return None
        
        import json
        args_str = json.dumps(self.tool_args or {}, sort_keys=True)
        key = f"{self.tool_name}:{args_str}"
        return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class LoopDetectionResult:
    """Result of loop detection analysis."""
    detected: bool = False
    loop_type: Optional[LoopType] = None
    confidence: float = 0.0
    detail: Optional[str] = None
    repetition_count: int = 0
    
    # Recovery recommendation
    suggested_escalation: Optional[RecoveryEscalationLevel] = None
    
    # For LLM-based detection
    confirmed_by_model: Optional[str] = None
    analysis: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/telemetry."""
        return {
            "detected": self.detected,
            "loop_type": self.loop_type.value if self.loop_type else None,
            "confidence": self.confidence,
            "detail": self.detail,
            "repetition_count": self.repetition_count,
            "suggested_escalation": self.suggested_escalation.value if self.suggested_escalation else None,
            "confirmed_by_model": self.confirmed_by_model,
            "analysis": self.analysis,
        }


# Type alias for detection callbacks
DetectionCallback = Callable[[LoopDetectionResult], Awaitable[None]]


class LoopDetectionEngine:
    """
    Multi-layer loop detection engine with pluggable detectors.
    
    Features:
    - Registers multiple detectors with weights
    - Combines detector results with weighted scoring
    - Tracks escalation across recovery attempts
    - Session-level disable capability
    - Telemetry hooks for all events
    
    Usage:
        engine = LoopDetectionEngine(config)
        
        # Process events
        result = await engine.check(event)
        
        if result.detected:
            action = engine.get_recovery_action(result)
            # Apply recovery action
    """
    
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._detectors: List[tuple] = []  # (detector, weight)
        self._disabled_sessions: Set[str] = set()
        self._session_states: Dict[str, Dict[str, Any]] = {}
        self._on_detection: List[DetectionCallback] = []
        
        # Initialize default detectors
        self._setup_default_detectors()
    
    def _setup_default_detectors(self) -> None:
        """Register default detectors."""
        from logicore.runtime.loop_detection.detectors import (
            ConsecutiveToolCallDetector,
            ContentRepetitionDetector,
            StagnantStateDetector,
        )
        
        # Register with weights (higher = more important)
        self.register_detector(
            ConsecutiveToolCallDetector(
                threshold=self.config.loop_detection.tool_call_threshold
            ),
            weight=1.0,
        )
        self.register_detector(
            ContentRepetitionDetector(
                threshold=self.config.loop_detection.content_repetition_threshold,
                chunk_size=self.config.loop_detection.content_chunk_size,
                max_history=self.config.loop_detection.max_content_history,
            ),
            weight=0.8,
        )
        self.register_detector(
            StagnantStateDetector(
                threshold=self.config.loop_detection.stagnant_turns_threshold
            ),
            weight=0.6,
        )
    
    def register_detector(self, detector: "LoopDetector", weight: float = 1.0) -> None:
        """
        Register a loop detector with a weight.
        
        Args:
            detector: Detector instance
            weight: Weight for combining results (0.0-1.0)
        """
        self._detectors.append((detector, weight))
    
    def register_on_detection(self, callback: DetectionCallback) -> None:
        """Register callback for when a loop is detected."""
        self._on_detection.append(callback)
    
    def disable_for_session(self, session_id: str) -> None:
        """Disable loop detection for a session."""
        self._disabled_sessions.add(session_id)
    
    def enable_for_session(self, session_id: str) -> None:
        """Re-enable loop detection for a session."""
        self._disabled_sessions.discard(session_id)
    
    def is_disabled(self, session_id: str) -> bool:
        """Check if loop detection is disabled for a session."""
        return (
            not self.config.loop_detection.enabled or
            session_id in self._disabled_sessions
        )
    
    def _get_session_state(self, session_id: str) -> Dict[str, Any]:
        """Get or create session state for tracking."""
        if session_id not in self._session_states:
            self._session_states[session_id] = {
                "recovery_attempts": 0,
                "escalation_level": 0,
                "last_loop_type": None,
                "cooled_down_tools": {},  # tool_name -> cooldown_until
            }
        return self._session_states[session_id]
    
    async def check(
        self,
        event: AgentEvent,
        session_id: str = "default",
    ) -> LoopDetectionResult:
        """
        Check an event for loop conditions.
        
        Args:
            event: Agent event to analyze
            session_id: Session for tracking
        
        Returns:
            LoopDetectionResult with detection status and recommendations
        """
        # Check if disabled
        if self.is_disabled(session_id):
            return LoopDetectionResult()
        
        # Run all detectors
        results: List[tuple] = []  # (result, weight)
        
        for detector, weight in self._detectors:
            try:
                result = await detector.check(event, session_id)
                if result.detected:
                    results.append((result, weight))
            except Exception:
                # Don't let detector failures break execution
                pass
        
        # No detections
        if not results:
            return LoopDetectionResult()
        
        # Find highest-confidence detection
        best_result, best_weight = max(results, key=lambda x: x[0].confidence * x[1])
        
        # Calculate combined confidence
        total_weight = sum(w for _, w in results)
        weighted_confidence = sum(r.confidence * w for r, w in results) / total_weight
        
        # Update with weighted confidence
        best_result.confidence = min(1.0, weighted_confidence * 1.2)  # Boost for multiple detections
        
        # Determine escalation level based on session state
        state = self._get_session_state(session_id)
        state["recovery_attempts"] += 1
        state["last_loop_type"] = best_result.loop_type
        
        # Calculate escalation level
        attempts_per_level = self.config.loop_detection.max_recovery_attempts_per_level
        escalation_index = min(
            state["recovery_attempts"] // attempts_per_level,
            len(self.config.loop_detection.escalation_levels) - 1,
        )
        best_result.suggested_escalation = self.config.loop_detection.escalation_levels[escalation_index]
        
        # Fire detection callbacks
        for callback in self._on_detection:
            try:
                await callback(best_result)
            except Exception:
                pass
        
        return best_result
    
    async def check_with_llm(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        user_prompt: Optional[str] = None,
        llm_provider: Optional[Any] = None,
    ) -> LoopDetectionResult:
        """
        Perform LLM-based loop detection.
        
        This is more expensive but catches semantic loops that
        hash-based detection misses.
        
        Args:
            messages: Recent conversation history
            session_id: Session identifier
            user_prompt: Original user request (for context)
            llm_provider: LLM provider for analysis
        
        Returns:
            LoopDetectionResult with LLM analysis
        """
        if self.is_disabled(session_id) or not llm_provider:
            return LoopDetectionResult()
        
        # Build analysis prompt (inspired by gemini-cli)
        analysis_prompt = self._build_loop_analysis_prompt(messages, user_prompt)
        
        try:
            response = await llm_provider.chat([
                {"role": "system", "content": self._get_loop_detection_system_prompt()},
                {"role": "user", "content": analysis_prompt},
            ])
            
            # Parse response
            result = self._parse_llm_loop_response(response)
            
            if result.detected:
                result.loop_type = LoopType.SEMANTIC_LOOP
                result.confirmed_by_model = getattr(llm_provider, "model_name", "unknown")
                
                # Fire callbacks
                for callback in self._on_detection:
                    try:
                        await callback(result)
                    except Exception:
                        pass
            
            return result
            
        except Exception:
            return LoopDetectionResult()
    
    def _get_loop_detection_system_prompt(self) -> str:
        """Get system prompt for LLM-based loop detection."""
        return """You are a diagnostic agent that determines whether a conversational AI assistant is stuck in an unproductive loop.

An unproductive state requires BOTH:
1. A repetitive pattern over at least 5 consecutive model actions
2. NO net change or forward progress toward the user's goal

Patterns to look for:
- Alternating cycles with no net effect (same edit/error cycle)
- Semantic repetition with identical outcomes
- Stuck reasoning (restating same plan without action)

What is NOT a loop:
- Cross-file batch operations (different files = progress)
- Incremental same-file edits (different line ranges = progress)
- Retry with variation (different approach = progress)

Respond with JSON:
{
    "is_loop": true/false,
    "confidence": 0.0-1.0,
    "analysis": "Your reasoning"
}"""
    
    def _build_loop_analysis_prompt(
        self,
        messages: List[Dict[str, Any]],
        user_prompt: Optional[str],
    ) -> str:
        """Build prompt for loop analysis."""
        # Truncate to recent history
        recent_count = self.config.loop_detection.llm_check_interval * 2
        recent = messages[-recent_count:] if len(messages) > recent_count else messages
        
        # Format messages
        formatted = []
        for msg in recent:
            role = msg.get("role", "unknown").upper()
            content = str(msg.get("content", ""))[:500]  # Truncate long content
            formatted.append(f"{role}: {content}")
        
        history_text = "\n".join(formatted)
        
        prompt = "Analyze this conversation for unproductive loops:\n\n"
        if user_prompt:
            prompt += f"ORIGINAL REQUEST: {user_prompt}\n\n"
        prompt += f"RECENT HISTORY:\n{history_text}\n\n"
        prompt += "Is this conversation stuck in an unproductive loop?"
        
        return prompt
    
    def _parse_llm_loop_response(self, response: Any) -> LoopDetectionResult:
        """Parse LLM response for loop detection."""
        import json
        
        try:
            content = getattr(response, "content", str(response))
            
            # Try to extract JSON
            if "{" in content:
                json_start = content.index("{")
                json_end = content.rindex("}") + 1
                data = json.loads(content[json_start:json_end])
                
                return LoopDetectionResult(
                    detected=data.get("is_loop", False),
                    confidence=float(data.get("confidence", 0.0)),
                    analysis=data.get("analysis"),
                )
        except Exception:
            pass
        
        return LoopDetectionResult()
    
    def reset_session(self, session_id: str) -> None:
        """Reset loop detection state for a session."""
        self._session_states.pop(session_id, None)
        self._disabled_sessions.discard(session_id)
        
        # Reset all detectors
        for detector, _ in self._detectors:
            detector.reset_session(session_id)
    
    def get_cooled_down_tools(self, session_id: str) -> List[str]:
        """Get list of tools currently in cooldown for a session."""
        state = self._get_session_state(session_id)
        now = time.time()
        
        cooled = []
        for tool_name, cooldown_until in state.get("cooled_down_tools", {}).items():
            if cooldown_until > now:
                cooled.append(tool_name)
        
        return cooled
    
    def apply_tool_cooldown(
        self,
        session_id: str,
        tool_name: str,
        duration_seconds: Optional[int] = None,
    ) -> None:
        """Apply cooldown to a tool for a session."""
        duration = duration_seconds or self.config.tool.default_cooldown_seconds
        state = self._get_session_state(session_id)
        
        if "cooled_down_tools" not in state:
            state["cooled_down_tools"] = {}
        
        state["cooled_down_tools"][tool_name] = time.time() + duration
    
    def is_tool_cooled_down(self, session_id: str, tool_name: str) -> bool:
        """Check if a tool is currently in cooldown."""
        state = self._get_session_state(session_id)
        cooldown_until = state.get("cooled_down_tools", {}).get(tool_name, 0)
        return time.time() < cooldown_until
