"""
ProviderFactory: Extracted provider creation logic from Agent.

Centralizes provider instantiation and makes it extensible.
"""

from typing import Optional, Dict, Type
import logging

from logicore.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Provider registry - maps provider names to their classes
_PROVIDER_REGISTRY: Dict[str, Type[LLMProvider]] = {}


def register_provider(name: str, provider_class: Type[LLMProvider]):
    """Register a custom provider class."""
    _PROVIDER_REGISTRY[name.lower()] = provider_class


def get_provider_names():
    """Get list of all registered provider names."""
    return list(_PROVIDER_REGISTRY.keys())


def create_provider(
    provider_name: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> LLMProvider:
    """
    Factory method to create providers from strings.
    
    The returned provider instance will be wrapped by a ProviderGateway.
    
    Supported providers:
    - ollama: Local Ollama models
    - groq: Groq API
    - gemini: Google Gemini
    - azure: Azure OpenAI
    - openai: OpenAI API
    - custom: Custom provider with endpoint
    
    To add a new provider:
    1. Create the provider class in logicore/providers/
    2. Register it: register_provider("myprovider", MyProvider)
    3. Or add it to the _PROVIDER_REGISTRY below
    """
    provider_name = provider_name.lower()
    
    # Check custom registry first
    if provider_name in _PROVIDER_REGISTRY:
        cls = _PROVIDER_REGISTRY[provider_name]
        kwargs = {"model_name": model}
        if api_key:
            kwargs["api_key"] = api_key
        if endpoint:
            kwargs["endpoint"] = endpoint
        return cls(**kwargs)
    
    # Built-in providers
    if provider_name == "ollama":
        from logicore.providers.ollama_provider import OllamaProvider
        return OllamaProvider(model_name=model or "gpt-oss:20b-cloud")
    
    elif provider_name == "groq":
        from logicore.providers.groq_provider import GroqProvider
        return GroqProvider(model_name=model or "llama-3.3-70b-versatile", api_key=api_key)
    
    elif provider_name == "gemini":
        from logicore.providers.gemini_provider import GeminiProvider
        return GeminiProvider(model_name=model or "gemini-pro", api_key=api_key)
    
    elif provider_name == "azure":
        from logicore.providers.azure_provider import AzureProvider
        return AzureProvider(model_name=model, api_key=api_key, endpoint=endpoint)
    
    elif provider_name == "openai":
        from logicore.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(model_name=model or "gpt-4", api_key=api_key)
    
    elif provider_name == "custom":
        from logicore.providers.custom_provider import CustomProvider
        return CustomProvider(model_name=model, api_key=api_key, endpoint=endpoint)
    
    else:
        supported = list(_PROVIDER_REGISTRY.keys()) + [
            "ollama", "groq", "gemini", "azure", "openai", "custom"
        ]
        raise ValueError(f"Unknown provider: {provider_name}. Supported: {supported}")
