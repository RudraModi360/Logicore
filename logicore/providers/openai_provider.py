from typing import Optional, Dict, Any
from .base import LLMProvider
from logicore.config.settings import get_api_key


class OpenAIProvider(LLMProvider):
    """Thin OpenAI SDK wrapper — config + client only.
    
    All message formatting, SDK calls, and response normalization
    are handled by OpenAIGateway in gateway.py.
    
    Supports custom endpoints for OpenAI-compatible servers (vLLM, LM Studio, etc.):
        provider = OpenAIProvider(model_name="gpt-4", api_key="xxx", endpoint="http://localhost:8000/v1")
    """
    provider_name = "openai"

    def __init__(self, model_name: str, api_key: Optional[str] = None, endpoint: Optional[str] = None,
                 model_config: Optional[Dict[str, Any]] = None, **kwargs):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        self.model_name = model_name
        self.api_key = api_key or get_api_key("openai")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set api_key or OPENAI_API_KEY env var.")
        
        self.endpoint = endpoint
        client_kwargs = {"api_key": self.api_key, "timeout": 120.0}
        if endpoint:
            client_kwargs["base_url"] = endpoint
        self.client = OpenAI(**client_kwargs, **kwargs)
        self.setup_model_profile(model_config)

    def get_model_name(self) -> str:
        return self.model_name
