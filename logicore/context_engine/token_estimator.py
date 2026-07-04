"""
TokenEstimator: Centralized token counting for the context engine.

Replaces ad-hoc `chars // 4` estimates scattered across the codebase.
Provides a single source of truth for token measurement.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional, Callable

# === Model context windows ===
MODEL_CONTEXT_WINDOWS = {
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 128000,
    "gpt-3.5-turbo": 16385,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-4-opus": 200000,
    "gemini-pro": 32000,
    "gemini-1.5-pro": 1000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.5-pro": 1000000,
    "llama3": 8192,
    "llama3.1": 128000,
    "llama3.2": 128000,
    "mistral": 8192,
    "mixtral": 32768,
    "qwen": 32768,
    "gpt-oss": 128000,
    "gpt-oss:20b-cloud": 128000,
    "default": 4096,
}


def get_model_context_window(model_name: str) -> int:
    """
    Get context window size for a model.
    Tries: exact match → prefix match → contains match → default.
    """
    if model_name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_name]
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if model_name.startswith(known):
            return window
    model_lower = model_name.lower()
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if known.lower() in model_lower:
            return window
    return MODEL_CONTEXT_WINDOWS["default"]


def estimate_tokens(text: str) -> int:
    """Estimate token count from text (~4 chars per token)."""
    if not text:
        return 0
    return len(text) // 4


def estimate_message_tokens(messages: list) -> int:
    """Estimate tokens for a list of messages."""
    total = 0
    for msg in messages:
        total += 4  # Role overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += 1000
        if "tool_calls" in msg:
            total += len(str(msg["tool_calls"])) // 10
    return total


class TokenEstimator:
    """
    Estimates token counts for messages and text.

    Uses character-length heuristic by default (~4 chars per token).
    Accepts a custom counter for model-specific tokenizers.
    """

    def __init__(self, token_counter: Optional[Callable[[str], int]] = None):
        self._counter = token_counter or self._default_counter

    @staticmethod
    def _default_counter(text: str) -> int:
        """Default: ~4 characters per token."""
        if not text:
            return 0
        return len(text) // 4

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        return self._counter(text)

    def count_message_tokens(self, msg: Dict[str, Any]) -> int:
        """Estimate tokens in a single message."""
        total = 0
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Role overhead (~4 tokens)
        total += 4

        # Content tokens
        if isinstance(content, str):
            total += self._counter(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if "text" in part:
                        total += self._counter(part["text"])
                    elif "type" in part and part["type"] == "image_url":
                        total += 1000  # Image rough estimate
                elif isinstance(part, str):
                    total += self._counter(part)

        # Tool calls overhead
        if "tool_calls" in msg:
            total += len(str(msg["tool_calls"])) // 10

        return total

    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens across a list of messages."""
        return sum(self.count_message_tokens(m) for m in messages)

    def categorize_tokens(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Categorize tokens by type.

        Returns dict with keys: system, tools, tool_results, messages.
        """
        categories = {"system": 0, "tools": 0, "tool_results": 0, "messages": 0}

        for msg in messages:
            role = msg.get("role", "")
            tokens = self.count_message_tokens(msg)

            if role == "system":
                categories["system"] += tokens
            elif role == "tool":
                categories["tool_results"] += tokens
            else:
                categories["messages"] += tokens

        return categories
