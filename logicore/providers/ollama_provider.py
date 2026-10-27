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
        explicit_context = (
            model_config.get("context_length")
            if isinstance(model_config, dict)
            else None
        )
        if isinstance(explicit_context, int) and explicit_context > 0:
            self.context_window = explicit_context
            self.context_window_source = "config"
        else:
            self.context_window = self._discover_context_window()
            self.context_window_source = "ollama_ps" if self.context_window else "profile"
        self.setup_model_profile(model_config)

    def _discover_context_window(self) -> Optional[int]:
        """Read the loaded model's context limit from Ollama when available."""
        try:
            response = self.client.ps()
        except Exception:
            return None

        models = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
        for model in models or []:
            name = model.get("model") if isinstance(model, dict) else getattr(model, "model", None)
            if name != self.model_name:
                continue
            context_length = (
                model.get("context_length")
                if isinstance(model, dict)
                else getattr(model, "context_length", None)
            )
            if isinstance(context_length, int) and context_length > 0:
                print(f"[Ollama] Model: {name} | Context: {context_length}")
                return context_length
        return None

    def get_context_window(self) -> Optional[int]:
        """Return the effective Ollama context window used for requests."""
        return getattr(self, "context_window", None)

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
