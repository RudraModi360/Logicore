import os
from typing import List, Dict, Any, Optional, Callable
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """Thin OpenAI SDK wrapper — config + client only.
    
    All message formatting, SDK calls, and response normalization
    are handled by OpenAIGateway in gateway.py.
    """
    provider_name = "openai"

    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        self.model_name = model_name
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set api_key or OPENAI_API_KEY env var.")
        self.client = OpenAI(api_key=self.api_key, **kwargs)

    def get_model_name(self) -> str:
        return self.model_name
