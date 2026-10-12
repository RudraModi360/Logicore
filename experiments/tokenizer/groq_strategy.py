"""Groq strategy — HF AutoTokenizer with model-family routing.

Uses apply_chat_template (not plain encode) so special tokens that matter
for Llama/Mixtral/Gemma are counted. Falls back to HeuristicStrategy when
the HF repo is gated/offline or the model family is unknown.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import TokenizerStrategy, _message_text
from .heuristic_strategy import HeuristicStrategy
from .hf_loader import (
    GROQ_FAMILY_MAP,
    GATED_OR_OFFLINE_WARNING,
    count_text_with_tokenizer,
    count_with_chat_template,
    load_hf_tokenizer,
    resolve_family,
)

WHISPER_WARNING = "whisper models are non-text — returning 0"


class GroqStrategy(TokenizerStrategy):
    """HF-chat-template counting for Groq model families."""

    provider = "groq"

    def __init__(self, model: str = "", hf_model_id: str = ""):
        self.model = model
        self.hf_model_id = hf_model_id or resolve_family(model, GROQ_FAMILY_MAP) or ""
        self._tokenizer = None
        self._loaded = False
        self._fell_back = False
        self._fallback = HeuristicStrategy(provider="groq", model=model)

    @property
    def accuracy_class(self) -> str:
        if self._is_non_text:
            return "estimated"
        if self._tokenizer_ready:
            return "high"
        return "estimated"

    @property
    def _is_non_text(self) -> bool:
        return (self.model or "").lower().startswith("whisper")

    @property
    def _tokenizer_ready(self) -> bool:
        self._ensure_loaded()
        return self._tokenizer is not None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.hf_model_id:
            self._tokenizer = load_hf_tokenizer(self.hf_model_id)
            if self._tokenizer is None:
                self._fell_back = True

    def count(self, text: str) -> int:
        if self._is_non_text:
            return 0
        self._ensure_loaded()
        if self._tokenizer is None:
            return self._fallback.count(text)
        return count_text_with_tokenizer(self._tokenizer, text)

    def count_message(self, msg: Dict[str, Any]) -> int:
        if self._is_non_text:
            return 0
        self._ensure_loaded()
        if self._tokenizer is None:
            return self._fallback.count_message(msg)
        text = _message_text(msg)
        ids = count_with_chat_template(self._tokenizer, [msg])
        if ids is not None:
            return ids
        return count_text_with_tokenizer(self._tokenizer, text)

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        if self._is_non_text:
            return 0
        self._ensure_loaded()
        if self._tokenizer is None:
            return self._fallback.count_messages(messages)
        ids = count_with_chat_template(self._tokenizer, messages)
        if ids is not None:
            return ids
        return sum(self.count_message(m) for m in (messages or []))

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["hf_model_id"] = self.hf_model_id or None
        if self._is_non_text:
            payload["warning"] = WHISPER_WARNING
        elif self._fell_back or not self.hf_model_id:
            payload["warning"] = GATED_OR_OFFLINE_WARNING
        return payload
