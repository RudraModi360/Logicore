import ollama
from typing import Optional, Dict, Any
from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Thin Ollama SDK wrapper — config + client only.
    
    All message formatting, SDK calls, and response normalization
    are handled by OllamaGateway in gateway.py.
    """
    provider_name = "ollama"

    def __init__(self, model_name: str, api_key: Optional[str] = None, endpoint: Optional[str] = None,
                 model_config: Optional[Dict[str, Any]] = None, **kwargs):
        self.model_name = model_name
        self.endpoint = endpoint
        if endpoint:
            kwargs["host"] = endpoint
        self.client = ollama.Client(**kwargs)
        self.setup_model_profile(model_config)

    def get_model_name(self) -> str:
        return self.model_name

    def pull_model(self) -> bool:
        """Pulls the model if it's not already present locally."""
        try:
            local_models = self.client.list()
            model_exists = any(
                m['name'] == self.model_name or m['name'].startswith(f"{self.model_name}:")
                for m in local_models.get('models', [])
            )
            if not model_exists:
                print(f"[Ollama] Pulling model: {self.model_name}...")
                self.client.pull(self.model_name)
                return True
            return False
        except Exception as e:
            print(f"[Ollama] Error pulling model {self.model_name}: {e}")
            return False
