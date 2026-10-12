"""Ollama strategy — HF AutoTokenizer for known families, heuristic otherwise.

Ollama model names map to HF repos for estimation only (the actual modelfile
chat template may differ), so HF-backed counts are exactness="estimated"
with accuracy_class="high". gpt-oss* maps to tiktoken cl100k_base.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import TokenizerStrategy, _message_text
from .heuristic_strategy import HeuristicStrategy
from .hf_loader import (
    GATED_OR_OFFLINE_WARNING,
    OLLAMA_FAMILY_MAP,
    count_text_with_tokenizer,
    count_with_chat_template,
    load_hf_tokenizer,
    resolve_family,
)


class OllamaStrategy(TokenizerStrategy):
    """HF-chat-template (or heuristic) counting for Ollama-served models."""

    provider = "ollama"

    def __init__(self, model: str = ""):
        self.model = model
        name = (model or "").lower()
        self._use_tiktoken = name.startswith("gpt-oss")
        self.hf_model_id = "" if self._use_tiktoken else (resolve_family(name, OLLAMA_FAMILY_MAP) or "")
        self._tokenizer = None
        self._loaded = False
        self._fell_back = False
        self._fallback = HeuristicStrategy(provider="ollama", model=model)
        self._tiktoken_inner = None

    @property
    def accuracy_class(self) -> str:
        if self._use_tiktoken:
            return "high"
        self._ensure_loaded()
        return "high" if self._tokenizer is not None else "estimated"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._use_tiktoken:
            from .openai_strategy import OpenAIStrategy

            self._tiktoken_inner = OpenAIStrategy(model="", encoding_name="cl100k_base")
            return
        if self.hf_model_id:
            self._tokenizer = load_hf_tokenizer(self.hf_model_id)
            if self._tokenizer is None:
                self._fell_back = True

    def count(self, text: str) -> int:
        self._ensure_loaded()
        if self._use_tiktoken:
            return self._tiktoken_inner.count(text)
        if self._tokenizer is None:
            return self._fallback.count(text)
        return count_text_with_tokenizer(self._tokenizer, text)

    def count_message(self, msg: Dict[str, Any]) -> int:
        self._ensure_loaded()
        if self._use_tiktoken:
            return self._tiktoken_inner.count_message(msg)
        if self._tokenizer is None:
            return self._fallback.count_message(msg)
        ids = count_with_chat_template(self._tokenizer, [msg])
        if ids is not None:
            return ids
        return count_text_with_tokenizer(self._tokenizer, _message_text(msg))

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        self._ensure_loaded()
        if self._use_tiktoken:
            return self._tiktoken_inner.count_messages(messages)
        if self._tokenizer is None:
            return self._fallback.count_messages(messages)
        ids = count_with_chat_template(self._tokenizer, messages)
        if ids is not None:
            return ids
        return sum(self.count_message(m) for m in (messages or []))

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["hf_model_id"] = self.hf_model_id or None
        if self._use_tiktoken:
            payload["encoding"] = "cl100k_base"
        elif self._fell_back or not self.hf_model_id:
            payload["warning"] = GATED_OR_OFFLINE_WARNING
        return payload
