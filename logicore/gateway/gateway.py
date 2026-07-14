"""
Provider Gateway Layer — Backward-compatible re-export.

All gateway classes have been split into separate modules:
- base.py: NormalizedMessage, ProviderGateway, shared utilities
- openai_gateway.py: OpenAIGateway
- gemini_gateway.py: GeminiGateway
- ollama_gateway.py: OllamaGateway
- azure_gateway.py: AzureGateway
- resilient.py: ResilientGateway, get_gateway_for_provider, get_resilient_gateway

This module re-exports everything for backward compatibility.
"""

from .base import (
    NormalizedMessage,
    ProviderGateway,
    _gateway_debug,
    _dispatch_token,
    _dispatch_stream_text,
    _convert_local_images_to_base64,
    _normalize_openai_tool_calls,
    _accumulate_openai_stream_tool_calls,
)
from .openai_gateway import OpenAIGateway
from .gemini_gateway import GeminiGateway
from .ollama_gateway import OllamaGateway
from .azure_gateway import AzureGateway
from .resilient import (
    ResilientGateway,
    get_gateway_for_provider,
    get_resilient_gateway,
)

__all__ = [
    "NormalizedMessage",
    "ProviderGateway",
    "OpenAIGateway",
    "GeminiGateway",
    "OllamaGateway",
    "AzureGateway",
    "ResilientGateway",
    "get_gateway_for_provider",
    "get_resilient_gateway",
]
