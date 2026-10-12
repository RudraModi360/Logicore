from typing import Optional, Dict, Any
from .base import LLMProvider
from logicore.config.settings import get_api_key


class GroqProvider(LLMProvider):
    """Thin Groq SDK wrapper — config + client only.
    
    Uses OpenAI-compatible API. All message formatting, SDK calls,
    and response normalization are handled by OpenAIGateway in gateway.py.
    
    Also provides an OpenAI client for the Responses API (Groq SDK
    doesn't support responses endpoint).
    
    Supports custom endpoints for Groq-compatible servers:
        provider = GroqProvider(model_name="llama-3.3-70b", api_key="xxx", endpoint="http://localhost:8000")
    """
    provider_name = "groq"

    def __init__(self, model_name: str, api_key: Optional[str] = None, endpoint: Optional[str] = None,
                 model_config: Optional[Dict[str, Any]] = None, **kwargs):
        from groq import Groq

        self.model_name = model_name
        self.api_key = api_key or get_api_key("groq")
        if not self.api_key:
            raise ValueError("Groq API key is required.")
        
        self.endpoint = endpoint
        if endpoint:
            self.client = Groq(api_key=self.api_key, base_url=endpoint, timeout=120.0)
        else:
            self.client = Groq(api_key=self.api_key, timeout=120.0)

        # OpenAI client for Responses API (Groq SDK lacks responses endpoint)
        from openai import OpenAI
        base_url = endpoint or "https://api.groq.com/openai/v1"
        self._responses_client = OpenAI(
            api_key=self.api_key,
            base_url=base_url,
            timeout=120.0,
        )
        self.setup_model_profile(model_config)

    def get_model_name(self) -> str:
        return self.model_name
