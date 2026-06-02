from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .availability import HealthState, FailureCategory


class ProviderCapability(Enum):
    """Capabilities that a provider may support."""
    CHAT = "chat"
    STREAMING = "streaming"
    TOOLS = "tools"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    JSON_MODE = "json_mode"


class LLMProvider(ABC):
    """Base class for LLM providers.
    
    Providers are thin SDK wrappers that hold:
    - Client instance (e.g., ollama.Client, Groq, genai.Client)
    - Model configuration (model_name, api_key, endpoint)
    
    The ProviderGateway handles all message formatting, SDK calls,
    and response normalization. Provider.chat()/chat_stream() delegate
    to the gateway for backward compatibility.
    
    Health tracking and failover is managed by ModelAvailabilityService.
    """
    
    provider_name: str = "unknown"
    
    # Default capabilities (subclasses should override)
    _capabilities: set = {ProviderCapability.CHAT, ProviderCapability.STREAMING}

    @abstractmethod
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        pass
    
    def get_provider_id(self) -> str:
        """Get unique identifier for this provider instance."""
        return f"{self.provider_name}:{self.get_model_name()}"
    
    def supports(self, capability: ProviderCapability) -> bool:
        """Check if this provider supports a specific capability."""
        return capability in self._capabilities
    
    def get_capabilities(self) -> set:
        """Get all capabilities supported by this provider."""
        return self._capabilities.copy()

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
    
    async def health_check(self) -> bool:
        """
        Perform a lightweight health check.
        
        Default implementation tries a minimal chat request.
        Subclasses can override for provider-specific health checks.
        
        Returns:
            True if provider is healthy, False otherwise
        """
        try:
            # Minimal request to verify connectivity
            result = await self.chat([{"role": "user", "content": "ping"}])
            return result is not None
        except Exception:
            return False
    
    def get_metadata(self) -> Dict[str, Any]:
        """Get provider metadata for monitoring/debugging."""
        return {
            "provider_name": self.provider_name,
            "model_name": self.get_model_name(),
            "provider_id": self.get_provider_id(),
            "capabilities": [c.value for c in self._capabilities],
        }
