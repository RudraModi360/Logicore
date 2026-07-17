"""Feedback detection and correction handling for self-healing agents.

Modeled after Claude Code's ``withMemoryCorrectionHint`` and memory extraction
patterns (``src/memdir/`` + ``src/services/extractMemories/``).

When the agent makes a mistake or user provides correction:
1. Detect the correction in user's message
2. Inject a correction hint so the LLM can reason about it
3. Store the correction in session state for learning
4. Optionally extract operational memory for cross-session learning

This module is dependency-free so it can be unit-tested in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime


class CorrectionType(Enum):
    """Types of user corrections."""
    
    PREFERENCE = "preference"           # User prefers X over Y
    APPROACH = "approach"               # Don't do it that way, do this instead
    TOOL_CHOICE = "tool_choice"         # Wrong tool selected
    PARAMETER = "parameter"             # Wrong parameter value
    SCOPE = "scope"                     # Too much/too little work
    TIMING = "timing"                   # Wrong time to do something
    STYLE = "style"                     # Code style, naming, etc.
    FACTUAL = "factual"                 # Incorrect information
    UNDO = "undo"                       # User wants to undo a change
    OTHER = "other"                     # Catch-all


@dataclass
class DetectedCorrection:
    """A user correction detected in a message."""
    
    correction_type: CorrectionType
    original: str           # What was wrong (agent's action/understanding)
    corrected: str          # What the user wants instead
    confidence: float       # 0.0-1.0 detection confidence
    context: Optional[str] = None  # Additional context
    timestamp: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


@dataclass
class FeedbackDetectionResult:
    """Result of feedback detection analysis."""
    
    has_correction: bool
    corrections: List[DetectedCorrection] = field(default_factory=list)
    should_inject_hint: bool = False
    hint_message: Optional[str] = None
    
    @property
    def correction_count(self) -> int:
        return len(self.corrections)
    
    @property
    def primary_correction(self) -> Optional[DetectedCorrection]:
        """Get the highest-confidence correction."""
        if not self.corrections:
            return None
        return max(self.corrections, key=lambda c: c.confidence)


# Correction detection patterns — expanded
CORRECTION_PATTERNS = [
    # Direct corrections
    (r"don'?t\s+(?:do|use|try|attempt)\s+that", CorrectionType.APPROACH, 0.8),
    (r"instead\s+(?:of|try|use|do)", CorrectionType.APPROACH, 0.85),
    (r"wrong\s+(?:tool|approach|way|method)", CorrectionType.TOOL_CHOICE, 0.8),
    (r"(?:can'?t|cannot|won'?t|shouldn'?t)\s+(?:use|do|try)", CorrectionType.APPROACH, 0.7),
    (r"that'?s?\s+(?:not|wrong|incorrect|bad)", CorrectionType.FACTUAL, 0.75),
    (r"i\s+(?:prefer|want|like|need)\s+", CorrectionType.PREFERENCE, 0.8),
    (r"(?:use|try|do)\s+this\s+instead", CorrectionType.APPROACH, 0.85),
    (r"(?:stop|quit|cancel|undo)\s+(?:that|this|it)", CorrectionType.UNDO, 0.9),
    (r"that\s+(?:was|is)\s+(?:too\s+)?(?:much|little|big|small)", CorrectionType.SCOPE, 0.7),
    (r"(?:not|don'?t)\s+(?:like|want)\s+that", CorrectionType.STYLE, 0.75),
    (r"(?:change|fix|update)\s+(?:that|this|it)\s+to", CorrectionType.PARAMETER, 0.8),
    (r"(?:actually|in\s+fact|no)\s*,?\s+(?:it'?s?|use|do|try)", CorrectionType.APPROACH, 0.7),
    (r"(?:before|after)\s+(?:that|this|doing)", CorrectionType.TIMING, 0.65),
    # Expanded: negation of success
    (r"(?:no|that|it)\s+(?:didn'?t|doesn'?t|won'?t)\s+(?:work|succeed|help|fix|solve)", CorrectionType.FACTUAL, 0.75),
    (r"still\s+(?:broken|failing|wrong|not\s+working)", CorrectionType.FACTUAL, 0.8),
    (r"(?:that|it)\s+(?:made|caused|broke|ruined)\s+(?:it|things|everything)", CorrectionType.UNDO, 0.8),
    # Expanded: scope/framing corrections
    (r"(?:that|it|this)\s+(?:is|was)\s+(?:too\s+)?(?:much|aggressive|broad|narrow)", CorrectionType.SCOPE, 0.7),
    (r"just|only|simply|merely", CorrectionType.SCOPE, 0.5),
    (r"(?:don'?t|do\s+not)\s+(?:overcomplicate|overengineer|overdo)", CorrectionType.SCOPE, 0.75),
    # Expanded: tool choice
    (r"(?:wrong|bad|poor)\s+(?:tool|library|approach|choice)", CorrectionType.TOOL_CHOICE, 0.8),
    (r"don'?t\s+use\s+\w+", CorrectionType.TOOL_CHOICE, 0.7),
    (r"use\s+\w+\s+instead", CorrectionType.TOOL_CHOICE, 0.8),
    # Expanded: style/format
    (r"(?:format|style|structure)\s+(?:is|was)\s+wrong", CorrectionType.STYLE, 0.75),
    (r"(?:don'?t|do\s+not)\s+(?:like|want)\s+that\s+(?:format|style|structure)", CorrectionType.STYLE, 0.8),
    # Expanded: undo/stop
    (r"(?:undo|revert|rollback)\s+(?:that|this|the|last)", CorrectionType.UNDO, 0.9),
    (r"(?:don'?t|do\s+not)\s+(?:make|create|add|modify|change|edit)", CorrectionType.UNDO, 0.85),
    (r"(?:stop|cancel|abort|halt)\s+(?:the|this|that|what)", CorrectionType.UNDO, 0.9),
    # Expanded: factual accuracy
    (r"(?:that|it)\s+(?:is|was)\s+(?:not|no)\s+(?:true|correct|accurate|right)", CorrectionType.FACTUAL, 0.8),
    (r"(?:you|your)\s+(?:got|have|made)\s+(?:it|that|this)\s+wrong", CorrectionType.FACTUAL, 0.8),
    (r"(?:incorrect|inaccurate|false|mistaken)", CorrectionType.FACTUAL, 0.7),
    # Expanded: preference/directive
    (r"(?:always|never|please)\s+(?:use|do|try|avoid)", CorrectionType.PREFERENCE, 0.8),
    (r"(?:i'?d|would)\s+(?:prefer|like|rather)\s+(?:if|to|you)", CorrectionType.PREFERENCE, 0.8),
    (r"(?:from\s+now\s+on|next\s+time|going\s+forward)", CorrectionType.PREFERENCE, 0.75),
]

# Undo detection patterns (strong signals)
UNDO_PATTERNS = [
    r"(?:undo|revert|rollback)\s+(?:that|this|the|last)",
    r"(?:don'?t|do\s+not)\s+(?:make|create|add|modify|change|edit)",
    r"(?:stop|cancel|abort)\s+(?:the|this|that)",
    r"(?:go\s+back|reverse)\s+(?:that|this|the)",
]


class FeedbackDetector:
    """Detects user corrections and feedback in messages.
    
    Uses pattern matching with confidence scoring to identify
    when a user is correcting the agent's behavior.
    """
    
    def __init__(self, min_confidence: float = 0.6):
        """Initialize detector.
        
        Args:
            min_confidence: Minimum confidence threshold for detection
        """
        self.min_confidence = min_confidence
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), correction_type, base_confidence)
            for pattern, correction_type, base_confidence in CORRECTION_PATTERNS
        ]
        self._compiled_undo_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in UNDO_PATTERNS
        ]
    
    def detect(self, user_message: str, previous_agent_action: Optional[str] = None) -> FeedbackDetectionResult:
        """Detect corrections in a user message.
        
        Args:
            user_message: The user's message to analyze
            previous_agent_action: Optional description of what the agent just did
            
        Returns:
            FeedbackDetectionResult with detected corrections
        """
        if not user_message or not user_message.strip():
            return FeedbackDetectionResult(has_correction=False)
        
        corrections = []
        
        # Check for undo patterns (high confidence)
        for pattern in self._compiled_undo_patterns:
            if pattern.search(user_message):
                corrections.append(DetectedCorrection(
                    correction_type=CorrectionType.UNDO,
                    original=previous_agent_action or "previous action",
                    corrected="undo/revert the action",
                    confidence=0.9,
                    context=user_message[:200]
                ))
        
        # Check for other correction patterns
        for pattern, correction_type, base_confidence in self._compiled_patterns:
            match = pattern.search(user_message)
            if match:
                # Boost confidence if we have context about what was wrong
                confidence = base_confidence
                if previous_agent_action:
                    confidence = min(confidence + 0.1, 0.95)
                
                corrections.append(DetectedCorrection(
                    correction_type=correction_type,
                    original=previous_agent_action or "agent's previous approach",
                    corrected=user_message[:200],
                    confidence=confidence,
                    context=user_message[:200]
                ))
        
        # Filter by confidence
        valid_corrections = [c for c in corrections if c.confidence >= self.min_confidence]
        
        # Deduplicate by type (keep highest confidence)
        seen_types = {}
        for correction in valid_corrections:
            if correction.correction_type not in seen_types or \
               correction.confidence > seen_types[correction.correction_type].confidence:
                seen_types[correction.correction_type] = correction
        valid_corrections = list(seen_types.values())
        
        # Sort by confidence
        valid_corrections.sort(key=lambda c: c.confidence, reverse=True)
        
        has_correction = len(valid_corrections) > 0
        
        # Generate hint message if correction detected
        hint_message = None
        should_inject_hint = False
        if has_correction:
            hint_message = self._generate_hint(valid_corrections[0])
            should_inject_hint = True
        
        return FeedbackDetectionResult(
            has_correction=has_correction,
            corrections=valid_corrections,
            should_inject_hint=should_inject_hint,
            hint_message=hint_message,
        )
    
    def _generate_hint(self, correction: DetectedCorrection) -> str:
        """Generate a correction hint message for the LLM."""
        hints = {
            CorrectionType.PREFERENCE: (
                "Note: The user has expressed a preference. "
                "Pay close attention to what they want and adjust your approach accordingly. "
                "Consider saving this preference to memory for future reference."
            ),
            CorrectionType.APPROACH: (
                "Note: The user is correcting your approach. "
                "They don't want you to proceed this way. "
                "Listen carefully to their correction and try the suggested alternative."
            ),
            CorrectionType.TOOL_CHOICE: (
                "Note: The user is indicating you're using the wrong tool. "
                "Consider what tool would be more appropriate for this task."
            ),
            CorrectionType.PARAMETER: (
                "Note: The user wants you to change a parameter value. "
                "Pay attention to the specific value they're requesting."
            ),
            CorrectionType.SCOPE: (
                "Note: The user is adjusting the scope of your work. "
                "They think your approach is either too broad or too narrow."
            ),
            CorrectionType.TIMING: (
                "Note: The user wants you to change when you do something. "
                "Pay attention to the sequence or timing they're requesting."
            ),
            CorrectionType.STYLE: (
                "Note: The user has a style preference. "
                "Adjust your approach to match their preferred style."
            ),
            CorrectionType.FACTUAL: (
                "Note: The user is correcting an assumption or fact. "
                "Make sure you have the correct information before proceeding."
            ),
            CorrectionType.UNDO: (
                "Note: The user wants you to undo a previous action. "
                "Stop what you're doing and revert the change if possible."
            ),
            CorrectionType.OTHER: (
                "Note: The user is providing feedback. "
                "Pay close attention to what they're asking you to change."
            ),
        }
        
        base_hint = hints.get(correction.correction_type, hints[CorrectionType.OTHER])
        
        return (
            f"{base_hint}\n\n"
            f"The user's correction: \"{correction.corrected}\"\n"
            f"Your previous approach: \"{correction.original}\""
        )
    
    def get_correction_summary(self, corrections: List[DetectedCorrection]) -> str:
        """Generate a summary of detected corrections for logging/debugging."""
        if not corrections:
            return "No corrections detected"
        
        summaries = []
        for c in corrections:
            summaries.append(f"- {c.correction_type.value} (confidence: {c.confidence:.2f}): {c.corrected[:100]}")
        
        return f"Detected {len(corrections)} correction(s):\n" + "\n".join(summaries)

    async def detect_with_llm_fallback(
        self,
        user_message: str,
        previous_agent_action: Optional[str] = None,
        llm_call: Optional[callable] = None,
    ) -> FeedbackDetectionResult:
        """Detect corrections using patterns first, then LLM fallback.
        
        If pattern matching finds nothing but the message has a tone/sentiment
        that suggests correction (e.g., "that didn't help", "wrong answer"),
        use an LLM call to classify the correction semantically.
        
        Args:
            user_message: The user's message to analyze
            previous_agent_action: Optional description of what the agent just did
            llm_call: Async callable that takes a prompt and returns a response string.
                      If None, LLM fallback is skipped.
        
        Returns:
            FeedbackDetectionResult with detected corrections
        """
        # Phase 1: Try pattern-based detection (fast, no LLM cost)
        result = self.detect(user_message, previous_agent_action)
        if result.has_correction:
            return result
        
        # Phase 2: LLM fallback — only if we have a callable and message is substantive
        if llm_call is None or not user_message or len(user_message.strip()) < 10:
            return result
        
        # Check if the message has correction-like signals (sarcasm, frustration, etc.)
        tone_signals = re.compile(
            r"(?:thanks\s+but|no|nah|ugh|seriously|are\s+you\s+sure|that'?s?\s+not|"
            r"you\s+(?:didn'?t|haven'?t|missed)|still\s+(?:not|broken|wrong)|"
            r"that\s+(?:didn'?t|doesn'?t|won'?t))",
            re.IGNORECASE,
        )
        if not tone_signals.search(user_message):
            return result
        
        # LLM classification prompt
        prompt = (
            "Analyze whether this user message contains a correction or negative feedback "
            "about the agent's previous action. Respond with ONLY a JSON object:\n"
            '{"is_correction": bool, "correction_type": "preference|approach|tool_choice|'
            'parameter|scope|timing|style|factual|undo|other", "original_wrong": "string|null", '
            '"corrected_to": "string|null", "confidence": 0.0-1.0}\n\n'
            f"Previous agent action: {previous_agent_action or 'unknown'}\n"
            f"User message: {user_message[:500]}"
        )
        
        try:
            response = await llm_call(prompt)
            return self._parse_llm_correction_response(response, user_message)
        except Exception:
            return result

    def _parse_llm_correction_response(
        self, response: str, user_message: str
    ) -> FeedbackDetectionResult:
        """Parse LLM response into a FeedbackDetectionResult."""
        import json
        try:
            data = json.loads(response.strip())
            if not data.get("is_correction", False):
                return FeedbackDetectionResult(has_correction=False)
            
            correction_type_str = data.get("correction_type", "other")
            try:
                correction_type = CorrectionType(correction_type_str)
            except ValueError:
                correction_type = CorrectionType.OTHER
            
            confidence = float(data.get("confidence", 0.7))
            if confidence < self.min_confidence:
                return FeedbackDetectionResult(has_correction=False)
            
            correction = DetectedCorrection(
                correction_type=correction_type,
                original=data.get("original_wrong") or "agent's previous action",
                corrected=data.get("corrected_to") or user_message[:200],
                confidence=confidence,
                context=user_message[:200],
            )
            
            hint = self._generate_hint(correction)
            return FeedbackDetectionResult(
                has_correction=True,
                corrections=[correction],
                should_inject_hint=True,
                hint_message=hint,
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return FeedbackDetectionResult(has_correction=False)
