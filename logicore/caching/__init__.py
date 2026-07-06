"""
Prompt Caching Module
=====================

Isolated module for LLM prompt caching optimizations.

This module handles:
- Identifying cacheable message prefixes (system + tools)
- Adding cache control annotations for provider-specific APIs
- Tracking cache statistics for telemetry
- Provider-specific optimizations (OpenAI, Groq, Anthropic, Ollama)

Providers with native prefix caching:
- OpenAI: Automatic prefix caching (no explicit control needed)
- Groq: Automatic prefix caching
- Anthropic: Explicit cache_control breakpoints
- Ollama: Local inference, model stays loaded in GPU memory

Usage:
    from logicore.caching import PromptCacheManager, get_prompt_cache_manager
    
    # Initialize
    cache = get_prompt_cache_manager(enabled=True)
    
    # Before LLM call - annotate messages
    annotated = cache.annotate_messages(messages, tools)
    
    # After LLM call - record stats
    cache.record_request(tokens_saved=5000, cache_hit=True)
"""

from logicore.caching.prompt_cache import (
    PromptCacheManager,
    CacheStats,
    get_prompt_cache_manager,
)

__all__ = [
    "PromptCacheManager",
    "CacheStats",
    "get_prompt_cache_manager",
]
