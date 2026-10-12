"""OpenAI strategy — tiktoken o200k_base / cl100k_base selected per model.

Also used by Azure OpenAI (openai-style wire format). The OpenAI per-message
role overhead (+4, +3 name, +1 priming) is applied in count_message/count_messages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import TokenizerStrategy, _message_text

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except Exception:
    TIKTOKEN_AVAILABLE = False

O200K_PREFIXES = ("gpt-4o", "gpt-4.1", "o3", "o4")
P50K_PREFIXES = ("gpt-3.5-turbo-instruct", "davinci")
CL100K_FALLBACK_WARNING = (
    "Model not in tiktoken registry — fell back to cl100k_base (high accuracy, not exact)"
)


def _pick_encoding(model: str) -> tuple:
    """Return (encoding_name, is_exact, warning)."""
    if not TIKTOKEN_AVAILABLE:
        return ("cl100k_base", False, "tiktoken not installed")
    name = (model or "").lower()
    if name.startswith(O200K_PREFIXES):
        return ("o200k_base", True, "")
    if name.startswith(P50K_PREFIXES):
        return ("p50k_base", True, "")
    if name.startswith("gpt-4") or name.startswith("gpt-3.5") or name.startswith("o1"):
        return ("cl100k_base", True, "")
    if name.startswith("text-embedding"):
        return ("cl100k_base", True, "")
    try:
        enc = tiktoken.encoding_for_model(model)
        return (enc.name, True, "")
    except Exception:
        return ("cl100k_base", False, CL100K_FALLBACK_WARNING)


class OpenAIStrategy(TokenizerStrategy):
    """tiktoken-backed counting for OpenAI / Azure-OpenAI-style models."""

    provider = "openai"

    def __init__(self, model: str = "gpt-4o", encoding_name: Optional[str] = None):
        self.model = model
        self._warning = ""
        if encoding_name:
            self._encoding_name = encoding_name
            self._is_exact = True
        else:
            self._encoding_name, self._is_exact, self._warning = _pick_encoding(model)
        self._encoding = None

    @property
    def _enc(self):
        if self._encoding is None and TIKTOKEN_AVAILABLE:
            self._encoding = tiktoken.get_encoding(self._encoding_name)
        return self._encoding

    @property
    def accuracy_class(self) -> str:
        return "exact" if self._is_exact else "high"

    @property
    def encoding_name(self) -> str:
        return self._encoding_name

    def count(self, text: str) -> int:
        if not text:
            return 0
        enc = self._enc
        if enc is None:
            return max(1, len(text) // 4)
        return len(enc.encode(text, disallowed_special=()))

    def count_message(self, msg: Dict[str, Any]) -> int:
        tokens = self.count(_message_text(msg))
        tokens += 4
        if msg.get("name"):
            tokens += 3
        return tokens

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = sum(self.count_message(m) for m in (messages or []))
        return total + 3

    def count_tools(self, tool_defs: List[Dict[str, Any]]) -> int:
        return super().count_tools(tool_defs)

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["encoding"] = self._encoding_name
        if self._warning:
            payload["warning"] = self._warning
        return payload
