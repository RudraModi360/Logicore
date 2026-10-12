"""Custom strategy — configurable tokenizer for CustomProvider endpoints.

Reads a config dict:
    {"type": "hf"|"tiktoken"|"heuristic"|"native_api",
     "model_id": "...", "revision": "main",
     "encoding": "cl100k_base", "chars_per_token": 3.5}

Never silently approximates: unconfigured custom endpoints get the
HeuristicStrategy with an explicit warning.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import TokenCountResult, TokenizerStrategy
from .heuristic_strategy import HeuristicStrategy
from .hf_loader import GATED_OR_OFFLINE_WARNING, count_text_with_tokenizer, count_with_chat_template, load_hf_tokenizer
from .openai_strategy import OpenAIStrategy

UNCONFIGURED_WARNING = (
    "Custom provider has no tokenizer config — using heuristic (configure "
    "model_config['tokenizer'] for exact counts)"
)
NATIVE_DEFERRED_WARNING = "native_api tokenizer deferred — count unavailable until endpoint wired"


class CustomStrategy(TokenizerStrategy):
    """Pluggable strategy driven by the custom tokenizer config dict."""

    provider = "custom"

    def __init__(self, model: str = "", tokenizer_config: Optional[Dict[str, Any]] = None):
        self.model = model
        self.config = tokenizer_config or {}
        self._type = (self.config.get("type") or "heuristic").lower()
        self._inner: Optional[TokenizerStrategy] = None
        self._hf_tokenizer = None
        self._warning = ""
        self._build()

    def _build(self) -> None:
        if self._type == "hf":
            model_id = self.config.get("model_id", "")
            revision = self.config.get("revision", "main")
            self._hf_tokenizer = load_hf_tokenizer(model_id, revision=revision)
            if self._hf_tokenizer is None:
                self._warning = GATED_OR_OFFLINE_WARNING
                self._inner = HeuristicStrategy(provider="custom", model=self.model)
                self._type = "heuristic"
        elif self._type == "tiktoken":
            encoding = self.config.get("encoding", "cl100k_base")
            self._inner = OpenAIStrategy(model="", encoding_name=encoding)
        elif self._type == "native_api":
            self._warning = NATIVE_DEFERRED_WARNING
        else:
            chars = self.config.get("chars_per_token")
            self._inner = HeuristicStrategy(
                provider="custom", chars_per_token=chars, model=self.model
            )
            if not self.config:
                self._warning = UNCONFIGURED_WARNING

    @property
    def accuracy_class(self) -> str:
        if self._type == "hf":
            return "high" if self._hf_tokenizer is not None else "estimated"
        if self._type == "tiktoken":
            return "exact"
        if self._type == "native_api":
            return "unknown"
        return "estimated"

    def count(self, text: str) -> int:
        if self._type == "native_api":
            return 0
        if self._type == "hf" and self._hf_tokenizer is not None:
            return count_text_with_tokenizer(self._hf_tokenizer, text)
        return self._inner.count(text)

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        if self._type == "native_api":
            return 0
        if self._type == "hf" and self._hf_tokenizer is not None:
            ids = count_with_chat_template(self._hf_tokenizer, messages)
            if ids is not None:
                return ids
        return self._inner.count_messages(messages)

    def count_result(self, messages: List[Dict[str, Any]]) -> TokenCountResult:
        if self._type == "native_api":
            return TokenCountResult(
                input_tokens=None,
                method="unknown",
                exactness="unknown",
                model=self.model,
                warnings=[self._warning] if self._warning else [],
            )
        warnings = [self._warning] if self._warning else []
        method = "local_tokenizer" if self._type in ("hf", "tiktoken") else "heuristic"
        exactness = {
            "hf": "model_tokenizer",
            "tiktoken": "model_tokenizer",
            "heuristic": "estimated",
        }.get(self._type, "unknown")
        return TokenCountResult(
            input_tokens=self.count_messages(messages),
            method=method,
            exactness=exactness,
            model=self.model,
            warnings=warnings,
        )

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["tokenizer_type"] = self._type
        if self._warning:
            payload["warning"] = self._warning
        return payload
