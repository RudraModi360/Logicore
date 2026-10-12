"""Provider-aware TokenEstimator (experiments prototype of Component B).

Backward-compatible: TokenEstimator() with no args behaves exactly like the
current framework estimator (tiktoken cl100k if available, chars//4 if not).
When provider/model are given, delegates to the ProviderTokenizerRegistry.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except Exception:
    _TIKTOKEN_AVAILABLE = False


def _legacy_count(text: str) -> int:
    """Original framework behavior: tiktoken cl100k, else chars//4."""
    if not text:
        return 0
    if _TIKTOKEN_AVAILABLE:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


class TokenEstimator:
    """Counts tokens with provider-aware strategies; zero-arg stays legacy."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
        provider: str = "unknown",
        model: str = "default",
    ):
        self._explicit_counter = token_counter
        self.provider = provider or "unknown"
        self.model = model or "default"
        self._strategy = None
        if self._explicit_counter is None and self._is_provider_aware():
            try:
                from tokenizer import get_tokenizer
            except ImportError:
                from experiments.tokenizer import get_tokenizer

            self._strategy = get_tokenizer(self.provider, self.model)

    def _is_provider_aware(self) -> bool:
        return self.provider != "unknown" or self.model != "default"

    def _raw_count(self, text: str) -> int:
        if self._explicit_counter is not None:
            return self._explicit_counter(text)
        if self._strategy is not None:
            return self._strategy.count(text)
        return _legacy_count(text or "")

    def count_tokens(self, text: str) -> int:
        return self._raw_count(text or "")

    def count_tool_tokens(self, tool_defs: List[Dict[str, Any]]) -> int:
        if not tool_defs:
            return 0
        if self._strategy is not None:
            return self._strategy.count_tools(tool_defs)
        import json

        blob = json.dumps(tool_defs, separators=(",", ":"), ensure_ascii=False)
        return self._raw_count(blob)

    def count_message_tokens(self, message: Dict[str, Any]) -> int:
        if self._strategy is not None:
            return self._strategy.count_message(message)
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        return self._raw_count(content) + 4

    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        if self._strategy is not None:
            return self._strategy.count_messages(messages)
        return sum(self.count_message_tokens(m) for m in (messages or [])) + 3

    def categorize_tokens(self, messages: List[Dict[str, Any]]) -> Dict[str, int]:
        system = 0
        tool_defs = 0
        history = 0
        tool_results = 0
        for m in messages or []:
            role = m.get("role", "user")
            tokens = self.count_message_tokens(m)
            if role == "system":
                system += tokens
            elif role == "tool":
                tool_results += tokens
            else:
                history += tokens
        return {
            "system": system,
            "tool_defs": tool_defs,
            "messages": history,
            "tool_results": tool_results,
            "total": system + tool_defs + history + tool_results,
        }

    def get_accuracy_class(self) -> str:
        if self._explicit_counter is not None:
            return "exact"
        if self._strategy is not None:
            return self._strategy.accuracy_class
        return "high" if _TIKTOKEN_AVAILABLE else "estimated"

    def describe(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "accuracy_class": self.get_accuracy_class(),
        }
        if self._strategy is not None:
            payload.update(self._strategy.describe())
        elif self._explicit_counter is not None:
            payload["strategy"] = "explicit_counter"
        else:
            payload["strategy"] = "legacy_tiktoken_cl100k" if _TIKTOKEN_AVAILABLE else "legacy_chars_div_4"
        return payload
