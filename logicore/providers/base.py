from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable


class LLMProvider(ABC):
    """Base class for LLM providers.
    
    Providers are thin SDK wrappers that hold:
    - Client instance (e.g., ollama.Client, Groq, genai.Client)
    - Model configuration (model_name, api_key, endpoint)
    
    The ProviderGateway handles all message formatting, SDK calls,
    and response normalization. Provider.chat()/chat_stream() delegate
    to the gateway for backward compatibility.
    """
    
    provider_name: str = "unknown"

    @abstractmethod
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        pass

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """Chat via gateway delegation. Subclasses inherit this for free."""
        from .gateway import get_gateway_for_provider
        gw = get_gateway_for_provider(self)
        return await gw.chat(messages, tools=tools)

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> Any:
        """Streaming chat via gateway delegation. Subclasses inherit this for free."""
        from .gateway import get_gateway_for_provider
        gw = get_gateway_for_provider(self)
        return await gw.chat_stream(messages, tools=tools, on_token=on_token)
