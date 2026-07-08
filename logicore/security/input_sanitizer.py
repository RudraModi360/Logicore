"""
InputSanitizer: Protection against prompt injection attacks.

Validates and sanitizes user input before it reaches the LLM.
Detects common injection patterns and provides configurable responses.
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class InjectionAction(Enum):
    """Action to take when injection is detected."""
    BLOCK = "block"           # Block the message entirely
    WARN = "warn"             # Allow but log warning
    SANITIZE = "sanitize"     # Remove suspicious patterns


@dataclass
class SanitizationResult:
    """Result of input sanitization."""
    original: str
    sanitized: str
    is_suspicious: bool
    detected_patterns: List[str]
    action_taken: InjectionAction
    
    @property
    def was_blocked(self) -> bool:
        return self.action_taken == InjectionAction.BLOCK
    
    @property
    def was_modified(self) -> bool:
        return self.original != self.sanitized


class InputSanitizer:
    """
    Protects against prompt injection attacks.
    
    Detects:
    - System prompt override attempts
    - Role manipulation
    - Instruction injection
    - Delimiter escaping
    - Common jailbreak patterns
    """
    
    # Patterns that indicate prompt injection attempts
    INJECTION_PATTERNS = [
        # System prompt override attempts
        (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)", "system_override"),
        (r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)", "system_override"),
        (r"forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)", "system_override"),
        (r"you\s+are\s+now\s+(a|an|the)\s+", "role_override"),
        (r"act\s+as\s+(a|an|the)\s+", "role_override"),
        (r"pretend\s+you\s+are\s+", "role_override"),
        (r"new\s+(instructions?|role|persona)", "role_override"),
        
        # Role manipulation
        (r"<system>", "delimiter_escape"),
        (r"\[system\]", "delimiter_escape"),
        (r"###\s*system\s*###", "delimiter_escape"),
        (r"assistant:", "delimiter_escape"),
        (r"<assistant>", "delimiter_escape"),
        (r"\[assistant\]", "delimiter_escape"),
        
        # Instruction injection
        (r"output\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "instruction_extraction"),
        (r"print\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "instruction_extraction"),
        (r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "instruction_extraction"),
        (r"what\s+are\s+your\s+(system\s+)?(prompt|instructions?)", "instruction_extraction"),
        (r"show\s+me\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "instruction_extraction"),
        
        # Jailbreak patterns
        (r"DAN\s+mode", "jailbreak"),
        (r"developer\s+mode", "jailbreak"),
        (r"jailbreak", "jailbreak"),
        (r"bypass\s+(all\s+)?(filters?|restrictions?|rules?|limitations?)", "jailbreak"),
        (r"override\s+(all\s+)?(filters?|restrictions?|rules?|limitations?)", "jailbreak"),
        
        # Delimiter injection
        (r"```\s*system", "delimiter_escape"),
        (r"---\s*system", "delimiter_escape"),
        (r"==\s*system", "delimiter_escape"),
    ]
    
    def __init__(
        self,
        action: InjectionAction = InjectionAction.WARN,
        custom_patterns: Optional[List[Tuple[str, str]]] = None,
        allowed_overrides: Optional[List[str]] = None,
    ):
        """
        Args:
            action: Default action when injection is detected
            custom_patterns: Additional patterns to detect (regex, category)
            allowed_overrides: Patterns that are explicitly allowed
        """
        self.action = action
        self.patterns = self.INJECTION_PATTERNS.copy()
        if custom_patterns:
            self.patterns.extend(custom_patterns)
        self.allowed_overrides = allowed_overrides or []
        
        # Compile patterns for efficiency
        self._compiled = [
            (re.compile(pattern, re.IGNORECASE), category)
            for pattern, category in self.patterns
        ]
    
    def sanitize(self, text: str) -> SanitizationResult:
        """
        Check and optionally sanitize user input.
        
        Args:
            text: User input to check
            
        Returns:
            SanitizationResult with details about what was found
        """
        if not isinstance(text, str):
            return SanitizationResult(
                original=str(text),
                sanitized=str(text),
                is_suspicious=False,
                detected_patterns=[],
                action_taken=InjectionAction.WARN,
            )
        
        detected = []
        for pattern, category in self._compiled:
            if pattern.search(text):
                # Check if this pattern is in allowed overrides
                if not any(allowed in text.lower() for allowed in self.allowed_overrides):
                    detected.append(category)
        
        is_suspicious = len(detected) > 0
        
        if not is_suspicious:
            return SanitizationResult(
                original=text,
                sanitized=text,
                is_suspicious=False,
                detected_patterns=[],
                action_taken=InjectionAction.WARN,
            )
        
        # Determine action
        action = self._determine_action(detected)
        
        if action == InjectionAction.BLOCK:
            sanitized = "[Message blocked: potential prompt injection detected]"
        elif action == InjectionAction.SANITIZE:
            sanitized = self._remove_suspicious_patterns(text)
        else:
            sanitized = text
        
        logger.warning(
            f"[InputSanitizer] Suspicious input detected: {detected}. "
            f"Action: {action.value}"
        )
        
        return SanitizationResult(
            original=text,
            sanitized=sanitized,
            is_suspicious=True,
            detected_patterns=detected,
            action_taken=action,
        )
    
    def _determine_action(self, detected: List[str]) -> InjectionAction:
        """Determine action based on detected patterns."""
        # Critical patterns should always block. Delimiter-escape patterns are
        # included because unescaped <system>/[system]/```system fences let a
        # user smuggle instruction boundaries into the model context.
        critical = {"system_override", "role_override", "jailbreak", "delimiter_escape"}
        if any(p in critical for p in detected):
            return InjectionAction.BLOCK
        
        # Instruction extraction is suspicious but might be legitimate
        if "instruction_extraction" in detected:
            return InjectionAction.WARN
        
        # Use configured default for others
        return self.action
    
    def _remove_suspicious_patterns(self, text: str) -> str:
        """Remove suspicious patterns from text while preserving legitimate content."""
        result = text
        for pattern, _ in self._compiled:
            result = pattern.sub("[REDACTED]", result)
        return result
    
    def is_safe(self, text: str) -> bool:
        """Quick check if text is safe (no injection detected)."""
        result = self.sanitize(text)
        return not result.is_suspicious


def create_sanitizer(
    action: str = "warn",
    strict: bool = False,
) -> InputSanitizer:
    """
    Factory function to create a sanitizer with common configurations.
    
    Args:
        action: "block", "warn", or "sanitize"
        strict: If True, uses BLOCK action for all detections
    """
    action_map = {
        "block": InjectionAction.BLOCK,
        "warn": InjectionAction.WARN,
        "sanitize": InjectionAction.SANITIZE,
    }
    
    if strict:
        action = InjectionAction.BLOCK
    else:
        action = action_map.get(action, InjectionAction.WARN)
    
    return InputSanitizer(action=action)
