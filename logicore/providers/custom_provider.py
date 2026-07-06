import os
from typing import Optional
from .base import LLMProvider, ProviderCapability


class CustomProvider(LLMProvider):
    """Thin wrapper for any OpenAI-compatible API endpoint.

    Works with vLLM, LM Studio, text-generation-webui, Ollama (OpenAI mode),
    LocalAI, LiteLLM, and any other server exposing an OpenAI-compatible
    ``/v1/chat/completions`` endpoint.

    All message formatting, SDK calls, and response normalization are handled
    by OpenAIGateway in gateway.py (reused since the wire format is identical).

    Usage:
        provider = CustomProvider(
            model_name="qwen3:0.6b",
            api_key="not-needed",
            endpoint="http://localhost:1234/v1",
        )
        agent = Agent(llm=provider)

    Environment variables (fallback):
        CUSTOM_PROVIDER_MODEL       – model name
        CUSTOM_PROVIDER_API_KEY     – API key (optional for local servers)
        CUSTOM_PROVIDER_ENDPOINT    – base URL including /v1 if applicable
        CUSTOM_PROVIDER_CTX_WINDOW  – context window size (optional, for context management)
    """
    provider_name = "custom"

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        context_window: Optional[int] = None,
        **kwargs,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for CustomProvider. "
                "Run: pip install openai"
            )

        self.model_name = (
            model_name
            or os.environ.get("CUSTOM_PROVIDER_MODEL")
            or os.environ.get("CUSTOM_MODEL_NAME")
        )
        if not self.model_name:
            raise ValueError(
                "model_name is required. Pass it directly or set "
                "CUSTOM_PROVIDER_MODEL / CUSTOM_MODEL_NAME env var."
            )

        self.api_key = (
            api_key
            or os.environ.get("CUSTOM_PROVIDER_API_KEY")
            or os.environ.get("CUSTOM_API_KEY")
            or "not-needed"
        )

        self.endpoint = (
            endpoint
            or os.environ.get("CUSTOM_PROVIDER_ENDPOINT")
            or os.environ.get("CUSTOM_MODEL_ENDPOINT")
        )
        if not self.endpoint:
            raise ValueError(
                "endpoint is required. Pass it directly or set "
                "CUSTOM_PROVIDER_ENDPOINT / CUSTOM_MODEL_ENDPOINT env var."
            )

        # Context window for context management (optional, auto-detected if not set)
        self.context_window = (
            context_window
            or int(os.environ.get("CUSTOM_PROVIDER_CTX_WINDOW", 0))
            or None
        )

        # Build the OpenAI client pointed at the custom server.
        # Some local servers accept any API key, so we default to "not-needed".
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.endpoint,
            timeout=120.0,  # 2-minute timeout to prevent indefinite hangs
            **kwargs,
        )

    def get_model_name(self) -> str:
        return self.model_name

    def get_context_window(self) -> Optional[int]:
        """Get context window size if explicitly configured."""
        return self.context_window
