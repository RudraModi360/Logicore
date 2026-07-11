from typing import Optional
from .base import LLMProvider
from logicore.config.settings import get_api_key


class GroqProvider(LLMProvider):
    """Thin Groq SDK wrapper — config + client only.
    
    Uses OpenAI-compatible API. All message formatting, SDK calls,
    and response normalization are handled by OpenAIGateway in gateway.py.
    """
    provider_name = "groq"

    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        from groq import Groq

        self.model_name = model_name
        self.api_key = api_key or get_api_key("groq")
        if not self.api_key:
            raise ValueError("Groq API key is required.")
        self.client = Groq(api_key=self.api_key, timeout=120.0)

    def get_model_name(self) -> str:
        return self.model_name
