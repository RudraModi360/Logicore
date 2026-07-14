"""
Canonical telemetry contract for Logicore.

Provides a single normalized usage record that all provider-specific
raw API responses funnel through. Downstream consumers (cost, UI, DB,
rate-limit) only ever see the CanonicalUsage schema.

Key design decisions:
- Cache tokens are SEPARATE buckets, not folded into input.
  This enables cache hit % display and differential pricing.
- input_tokens = pure prompt tokens (cache excluded).
- prompt_tokens (property) = input + cache_read + cache_write.
- total_tokens (property) = prompt_tokens + output_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional


def _to_int(value: Any) -> int:
    """Safely convert a value to int. Handles None, strings, floats."""
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Safely convert a value to Decimal. Returns None if not convertible."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class CanonicalUsage:
    """
    Normalized usage record from a single API call.

    All provider-specific response shapes are normalized into these
    five token buckets plus a request count. Derived properties
    (prompt_tokens, total_tokens) maintain backward compatibility
    with legacy consumers.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    request_count: int = 1

    @property
    def prompt_tokens(self) -> int:
        """Total prompt tokens (input + cache)."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        """Total tokens (prompt + output)."""
        return self.prompt_tokens + self.output_tokens

    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary for serialization."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "request_count": self.request_count,
            "prompt_tokens": self.prompt_tokens,
            "total_tokens": self.total_tokens,
        }


def _get(value: Any, key: str, default: Any = 0) -> Any:
    """Get a field from either a dict or an object with attributes."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def normalize_usage(
    response_usage: Any,
    *,
    provider: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> CanonicalUsage:
    """
    Normalize raw provider usage into CanonicalUsage.

    Handles three distinct provider API shapes:
    1. Anthropic Messages — cache is separate top-level fields
    2. OpenAI/Codex Responses — cache is INCLUDED in input_tokens
    3. OpenAI Chat Completions — cache is INCLUDED in prompt_tokens
       (with fallback for OpenRouter/Cline proxy compatibility)

    Args:
        response_usage: Raw usage object/dict from the API response.
        provider: Provider name (e.g. "openai", "anthropic", "ollama").
        api_mode: API mode (e.g. "anthropic_messages", "codex_responses").

    Returns:
        CanonicalUsage with all five token buckets populated.
    """
    if response_usage is None:
        return CanonicalUsage()

    provider_lower = (provider or "").strip().lower()
    api_mode_lower = (api_mode or "").strip().lower()

    # Branch A: Anthropic Messages API
    # Cache fields are separate top-level attributes — no subtraction needed.
    if api_mode_lower == "anthropic_messages" or provider_lower == "anthropic":
        return _normalize_anthropic(response_usage)

    # Branch B: OpenAI Codex Responses API
    # input_tokens INCLUDES cache; details break it out.
    if api_mode_lower == "codex_responses":
        return _normalize_codex_responses(response_usage)

    # Branch C: OpenAI Chat Completions (default / fallback)
    # prompt_tokens INCLUDES cache; details break it out.
    # Also handles Ollama (OpenAI-compatible, no cache fields).
    return _normalize_openai_chat(response_usage)


def _normalize_anthropic(response_usage: Any) -> CanonicalUsage:
    """Normalize Anthropic Messages API usage.

    Anthropic returns cache as separate top-level fields:
    - input_tokens: pure input (cache excluded)
    - output_tokens: generated tokens
    - cache_read_input_tokens: tokens read from cache
    - cache_creation_input_tokens: tokens written to cache
    """
    input_tokens = _to_int(_get(response_usage, "input_tokens", 0))
    output_tokens = _to_int(_get(response_usage, "output_tokens", 0))
    cache_read = _to_int(_get(response_usage, "cache_read_input_tokens", 0))
    cache_write = _to_int(_get(response_usage, "cache_creation_input_tokens", 0))

    reasoning = _extract_reasoning(response_usage)

    return CanonicalUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
    )


def _normalize_codex_responses(response_usage: Any) -> CanonicalUsage:
    """Normalize OpenAI Codex Responses API usage.

    input_tokens INCLUDES cached tokens. The details object breaks them out:
    - input_tokens: total (includes cache)
    - input_tokens_details.cached_tokens: cache read
    - input_tokens_details.cache_creation_tokens: cache write
    - Pure input = total - cache_read - cache_write
    """
    input_total = _to_int(_get(response_usage, "input_tokens", 0))
    output_tokens = _to_int(_get(response_usage, "output_tokens", 0))

    details = _get(response_usage, "input_tokens_details", None)
    cache_read = _to_int(_get(details, "cached_tokens", 0) if details else 0)
    cache_write = _to_int(_get(details, "cache_creation_tokens", 0) if details else 0)

    input_tokens = max(0, input_total - cache_read - cache_write)
    reasoning = _extract_reasoning(response_usage)

    return CanonicalUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
    )


def _normalize_openai_chat(response_usage: Any) -> CanonicalUsage:
    """Normalize OpenAI Chat Completions usage.

    prompt_tokens INCLUDES cached tokens. The details object breaks them out:
    - prompt_tokens: total (includes cache)
    - prompt_tokens_details.cached_tokens: cache read
    - prompt_tokens_details.cache_write_tokens: cache write
    - Pure input = total - cache_read - cache_write

    Fallback: if prompt_tokens_details is missing, try top-level
    Anthropic-style fields (for OpenRouter/Cline proxy compatibility).
    """
    prompt_total = _to_int(_get(response_usage, "prompt_tokens", 0))
    output_tokens = _to_int(_get(response_usage, "completion_tokens", 0))

    # Try OpenAI-style details first
    details = _get(response_usage, "prompt_tokens_details", None)
    cache_read = _to_int(_get(details, "cached_tokens", 0) if details else 0)
    cache_write = _to_int(
        _get(details, "cache_write_tokens", 0) if details else 0
    )

    # Fallback: Anthropic-style top-level fields (OpenRouter/Cline proxy)
    if not cache_read:
        cache_read = _to_int(_get(response_usage, "cache_read_input_tokens", 0))
    if not cache_write:
        cache_write = _to_int(
            _get(response_usage, "cache_creation_input_tokens", 0)
        )

    input_tokens = max(0, prompt_total - cache_read - cache_write)
    reasoning = _extract_reasoning(response_usage)

    return CanonicalUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
    )


def _extract_reasoning(response_usage: Any) -> int:
    """Extract reasoning/thinking tokens from output details."""
    output_details = _get(response_usage, "output_tokens_details", None)
    if not output_details:
        output_details = _get(response_usage, "completion_tokens_details", None)
    if output_details:
        return _to_int(_get(output_details, "reasoning_tokens", 0))
    return 0
