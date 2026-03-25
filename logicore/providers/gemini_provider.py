import os
from typing import List, Dict, Any, Optional, Callable
from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Thin Google Gemini SDK wrapper — config + client only.
    
    All message formatting, SDK calls, and response normalization
    are handled by GeminiGateway in gateway.py.
    """
    provider_name = "gemini"

    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        from google import genai

        self.model_name = model_name
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini/Google API key is required.")
        self.client = genai.Client(api_key=self.api_key)

    def get_model_name(self) -> str:
        return self.model_name
