"""Anthropic strategy — tiktoken cl100k_base approximation (~3% error).

Anthropic does not publish a tokenizer; cl100k is the community-standard
approximation. Always carries a warning and exactness="estimated".
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import TokenizerStrategy, _message_text
from .openai_strategy import OpenAIStrategy

ANTHROPIC_WARNING = (
    "Anthropic tokenizer unavailable; using tiktoken cl100k approximation (~3% error)"
)
TURN_OVERHEAD = 12


class AnthropicStrategy(TokenizerStrategy):
    """tiktoken cl100k-backed counting that is honest about being an estimate."""

    provider = "anthropic"

    def __init__(self, model: str = ""):
        self.model = model
        self._inner = OpenAIStrategy(model="", encoding_name="cl100k_base")

    @property
    def accuracy_class(self) -> str:
        return "estimated"

    def count(self, text: str) -> int:
        return self._inner.count(text)

    def count_message(self, msg: Dict[str, Any]) -> int:
        return self._inner.count_message(msg)

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = sum(self.count_message(m) for m in (messages or []))
        return total + TURN_OVERHEAD

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["warning"] = ANTHROPIC_WARNING
        return payload
