"""Singleton registry: (provider, model) -> best available TokenizerStrategy.

Resolution order follows TOKEN_SYSTEM_SPEC section 3.3. Instances are cached
per (provider, model) because tokenizer loading is expensive.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .base import TokenizerStrategy
from .heuristic_strategy import HeuristicStrategy


class ProviderTokenizerRegistry:
    """Resolves and caches provider/model -> tokenizer strategy."""

    _instance: Optional["ProviderTokenizerRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
            cls._instance._overrides = {}
            cls._instance._custom_tokenizer_config = None
        return cls._instance

    def set_custom_tokenizer_config(self, config: Optional[Dict[str, Any]]) -> None:
        """Configure the tokenizer used for provider='custom' (see custom_strategy)."""
        self._custom_tokenizer_config = config
        for key in [k for k in self._cache if k[0] == "custom"]:
            del self._cache[key]

    def register(self, provider: str, model_pattern: str, strategy: TokenizerStrategy) -> None:
        """Register a strategy override for a provider + model prefix pattern."""
        self._overrides[(provider.lower(), model_pattern.lower())] = strategy

    def get(self, provider: str, model: str) -> TokenizerStrategy:
        provider = (provider or "unknown").lower()
        model = model or ""
        key = (provider, model)
        if key in self._cache:
            return self._cache[key]

        for (p, pattern), strategy in self._overrides.items():
            if p == provider and model.lower().startswith(pattern):
                self._cache[key] = strategy
                return strategy

        strategy = self._resolve(provider, model)
        self._cache[key] = strategy
        return strategy

    def _resolve(self, provider: str, model: str) -> TokenizerStrategy:
        if provider == "anthropic" or "claude" in model.lower():
            from .anthropic_strategy import AnthropicStrategy

            return AnthropicStrategy(model=model)

        if provider in ("openai", "azure"):
            from .openai_strategy import OpenAIStrategy

            return OpenAIStrategy(model=model)

        if provider == "gemini":
            from .google_strategy import GoogleStrategy

            return GoogleStrategy(model=model)

        if provider == "groq":
            from .groq_strategy import GroqStrategy

            return GroqStrategy(model=model)

        if provider == "ollama":
            from .ollama_strategy import OllamaStrategy

            return OllamaStrategy(model=model)

        if provider == "custom":
            from .custom_strategy import CustomStrategy

            return CustomStrategy(model=model, tokenizer_config=self._custom_tokenizer_config)

        return HeuristicStrategy(provider=provider or "unknown", model=model)

    def clear_cache(self) -> None:
        self._cache.clear()

    def describe(self, provider: str, model: str) -> Dict[str, Any]:
        return self.get(provider, model).describe()


def get_tokenizer(provider: str, model: str) -> TokenizerStrategy:
    """Module-level convenience: resolve (and cache) the best strategy."""
    return ProviderTokenizerRegistry().get(provider, model)
