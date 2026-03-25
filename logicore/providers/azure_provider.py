import os
import logging
from typing import List, Dict, Any, Optional
from .base import LLMProvider

logger = logging.getLogger("logicore.providers.azure")


class AzureProvider(LLMProvider):
    """Thin Azure AI SDK wrapper — config + client only.
    
    Supports:
    1. Azure OpenAI (GPT models)
    2. Azure AI Foundry - Anthropic (Claude models)
    3. Azure AI Inference (Llama, Mistral, Phi via MaaS)
    
    Auto-detects backend type from endpoint/model name.
    All chat/streaming logic handled by AzureGateway in gateway.py.
    """
    provider_name = "azure"

    MODEL_TYPE_OPENAI = "openai"
    MODEL_TYPE_ANTHROPIC = "anthropic"
    MODEL_TYPE_INFERENCE = "inference"

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        model_type: Optional[str] = None,
        **kwargs
    ):
        self.deployment_name = model_name
        self.api_key = api_key or os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
        self.endpoint = (endpoint or os.environ.get("AZURE_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        self.kwargs = kwargs

        if not self.api_key:
            raise ValueError("Azure API key is required. Provide api_key or set AZURE_API_KEY env var.")
        if not self.endpoint:
            raise ValueError("Azure Endpoint is required. Provide endpoint or set AZURE_ENDPOINT env var.")

        self.model_type = self._detect_model_type(model_type, model_name, self.endpoint)
        self.api_version = api_version or self._get_default_api_version()

        self.client = None
        self._init_client()

        logger.info(f"Initialized Azure {self.model_type} provider for deployment: {self.deployment_name}")

    def _detect_model_type(self, explicit_type: Optional[str], model_name: str, endpoint: str) -> str:
        """Heuristic-based detection of Azure deployment type."""
        if explicit_type:
            return explicit_type.lower()

        endpoint_lower = endpoint.lower()
        model_name_lower = model_name.lower()

        if "/v1" in endpoint_lower and "openai.azure.com" not in endpoint_lower:
            return self.MODEL_TYPE_INFERENCE
        if "anthropic" in endpoint_lower or "claude" in model_name_lower:
            return self.MODEL_TYPE_ANTHROPIC
        elif "openai.azure.com" in endpoint_lower or "gpt" in model_name_lower:
            return self.MODEL_TYPE_OPENAI
        elif "/models" in endpoint_lower or "inference" in endpoint_lower:
            return self.MODEL_TYPE_INFERENCE

        return self.MODEL_TYPE_OPENAI

    def _get_default_api_version(self) -> str:
        if self.model_type == self.MODEL_TYPE_OPENAI:
            return "2024-10-21"
        elif self.model_type == self.MODEL_TYPE_ANTHROPIC:
            return "2023-06-01"
        return "2024-05-01-preview"

    def _init_client(self):
        """Initialize the underlying SDK client based on self.model_type."""
        if self.model_type == self.MODEL_TYPE_ANTHROPIC:
            try:
                from anthropic import AnthropicFoundry
                base_url = self.endpoint
                if "/v1" in base_url:
                    base_url = base_url.split("/v1")[0]
                base_url = base_url.rstrip("/")
                self.client = AnthropicFoundry(api_key=self.api_key, base_url=base_url)
            except ImportError:
                logger.warning("Anthropic SDK not installed. Claude deployments may fail.")

        elif self.model_type == self.MODEL_TYPE_OPENAI:
            try:
                from openai import AzureOpenAI
                base_url = self.endpoint
                if "/openai/" in base_url:
                    base_url = base_url.split("/openai/")[0]
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=base_url,
                )
            except ImportError:
                logger.warning("OpenAI SDK not installed. OpenAI deployments may fail.")

        elif self.model_type == self.MODEL_TYPE_INFERENCE:
            try:
                from openai import AzureOpenAI
                base_ep = self.endpoint
                if "/openai/v1" in base_ep:
                    base_ep = base_ep.split("/openai/v1")[0]
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=base_ep,
                )
            except ImportError:
                logger.warning("OpenAI SDK not installed. Inference deployments may fail.")

    def get_model_name(self) -> str:
        return self.deployment_name
