from typing import Optional, Dict, Any
from .base import LLMProvider
from logicore.config.settings import get_api_key


class GeminiProvider(LLMProvider):
    """Thin Google Gemini SDK wrapper — config + client only.
    
    All message formatting, SDK calls, and response normalization
    are handled by GeminiGateway in gateway.py.
    
    Supports custom endpoints for Gemini-compatible servers:
        provider = GeminiProvider(model_name="gemini-pro", api_key="xxx", endpoint="http://localhost:8000")
    """
    provider_name = "gemini"

    def __init__(self, model_name: str, api_key: Optional[str] = None, endpoint: Optional[str] = None,
                 model_config: Optional[Dict[str, Any]] = None, **kwargs):
        from google import genai

        self.model_name = model_name
        self.api_key = api_key or get_api_key("gemini")
        if not self.api_key:
            raise ValueError("Gemini/Google API key is required.")
        
        self.endpoint = endpoint
        if endpoint:
            # For custom endpoints, use http_options to set the base URL
            from google.genai import types
            http_options = types.HttpOptions(base_url=endpoint)
            self.client = genai.Client(api_key=self.api_key, http_options=http_options)
        else:
            self.client = genai.Client(api_key=self.api_key)
        self.setup_model_profile(model_config)

    def get_model_name(self) -> str:
        return self.model_name
