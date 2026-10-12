"""Calibrated chars/token heuristic — the universal fallback strategy.

Never presents itself as exact. Always emits a warning. The chars//4 floor
is preserved so this works with no internet, no HF cache, no tiktoken.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .base import TokenizerStrategy, _message_text

PROSE_CHARS_PER_TOKEN = 4.0
CODE_CHARS_PER_TOKEN = 3.2
JSON_CHARS_PER_TOKEN = 2.8
MIXED_CHARS_PER_TOKEN = 3.5
ULTIMATE_FALLBACK_CHARS_PER_TOKEN = 4.0

PROVIDER_MULTIPLIERS = {
    "anthropic": 0.95,
    "gemini": 0.90,
    "groq": 1.0,
    "ollama": 1.05,
    "custom": 1.0,
    "unknown": 1.10,
}

CODE_MARKERS = ("def ", "function", "return ", "=>", "->", "import ", "class ", "printf", "console.log")
JSON_PUNCTUATION = set('{}",:[]')


class HeuristicStrategy(TokenizerStrategy):
    """Calibrated chars-per-token estimator for custom/unknown providers."""

    def __init__(
        self,
        provider: str = "unknown",
        chars_per_token: Optional[float] = None,
        model: str = "",
    ):
        self.provider = provider
        self.model = model
        self._override_chars_per_token = chars_per_token

    @property
    def accuracy_class(self) -> str:
        return "estimated"

    def _effective_chars_per_token(self, text: str) -> float:
        if self._override_chars_per_token:
            return self._override_chars_per_token
        base = self._detect_ratio(text)
        multiplier = PROVIDER_MULTIPLIERS.get(self.provider, PROVIDER_MULTIPLIERS["unknown"])
        return base * multiplier

    @staticmethod
    def _detect_ratio(text: str) -> float:
        if not text:
            return PROSE_CHARS_PER_TOKEN
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            punct = sum(1 for c in text if c in JSON_PUNCTUATION)
            if punct / max(len(text), 1) > 0.08:
                return JSON_CHARS_PER_TOKEN
            return MIXED_CHARS_PER_TOKEN
        lowered = text.lower()
        marker_hits = sum(1 for marker in CODE_MARKERS if marker in lowered)
        indent_hits = text.count("\n    ") + text.count("\n\t")
        if marker_hits >= 2 or indent_hits >= 3:
            return CODE_CHARS_PER_TOKEN
        return PROSE_CHARS_PER_TOKEN

    def count(self, text: str) -> int:
        if not text:
            return 0
        ratio = self._effective_chars_per_token(text)
        if ratio <= 0:
            return len(text) // ULTIMATE_FALLBACK_CHARS_PER_TOKEN
        return max(1, math.ceil(len(text) / ratio))

    def count_message(self, msg: Dict[str, Any]) -> int:
        text = _message_text(msg)
        overhead = 4 if msg.get("role") else 0
        return self.count(text) + overhead

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = sum(self.count_message(m) for m in (messages or []))
        return total + 1

    def warnings(self) -> List[str]:
        return [
            "Heuristic estimator used — accuracy class: estimated, target delta \u22645%"
        ]
