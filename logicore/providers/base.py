from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .availability import FailureCategory


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

    # Model profiling (Plan 01): every provider can carry its context-window
    # profile so it works standalone ("just the LLM connection + config")
    # without an Agent. Subclasses call setup_model_profile() in __init__.
    model_config: Optional[Dict[str, Any]] = None
    model_profile: Optional[Any] = None

    @abstractmethod
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        pass

    def setup_model_profile(self, model_config: Optional[Dict[str, Any]] = None) -> Any:
        """Attach a ModelProfile (context window, tier, masking ratios) to this provider.

        Modular by design: any provider can resolve its own profile at
        construction time. If ``model_config`` is None and the provider
        exposes a legacy ``context_window`` attribute (e.g. CustomProvider),
        that value is folded in as ``context_length``.

        Returns:
            The resolved ModelProfile.
        """
        from logicore.runtime.model_profile import ModelProfile

        effective = model_config
        if effective is None:
            legacy_window = getattr(self, "context_window", None)
            if legacy_window:
                effective = {"context_length": int(legacy_window)}
        name = self.get_model_name() or "unknown"
        profile = ModelProfile.resolve(name, effective)
        self.model_config = effective
        self.model_profile = profile
        return profile

    def get_model_profile(self) -> Optional[Any]:
        """Return the attached ModelProfile (None if never set up)."""
        return getattr(self, "model_profile", None)

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
        from logicore.gateway.gateway import get_gateway_for_provider
        gw = get_gateway_for_provider(self)
        return await gw.chat(messages, tools=tools)

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Any:
        """Streaming chat via gateway delegation. Subclasses inherit this for free."""
        from logicore.gateway.gateway import get_gateway_for_provider
        gw = get_gateway_for_provider(self)
        return await gw.chat_stream(messages, tools=tools, on_token=on_token, on_event=on_event)
    
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
        profile = getattr(self, "model_profile", None)
        return {
            "provider_name": self.provider_name,
            "model_name": self.get_model_name(),
            "provider_id": self.get_provider_id(),
            "capabilities": [c.value for c in self._capabilities],
            "model_profile": profile.to_dict() if profile is not None else None,
        }
