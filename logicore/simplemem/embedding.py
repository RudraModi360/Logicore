"""
Embedding model for SimpleMem.
Supports Ollama (primary) and HuggingFace (fallback).

Based on SimpleMem: https://github.com/aiming-lab/SimpleMem
"""
import os
import numpy as np
from typing import List, Optional, Union
import warnings

# Suppress tokenizer parallelism warning
os.environ["TOKENIZERS_PARALLELISM"] = "false"


class EmbeddingModel:
    """
    Embedding model with automatic backend detection.
    
    Priority:
    1. Ollama (fast, local, preferred for deployment)
    """
    
    # Model mappings
    OLLAMA_MODELS = {
        "qwen3-embedding:0.6b": 1024,
        "nomic-embed-text": 768,
        "all-minilm": 384,
        "mxbai-embed-large": 1024,
    }

    def __init__(
        self,
        model_name: str = None,
        ollama_base_url: str = "http://localhost:11434",
        fallback_to_hf: bool = True,
        debug: bool = False
    ):
        self.debug = debug
        self.ollama_base_url = ollama_base_url
        self.fallback_to_hf = fallback_to_hf
        
        # Determine model from config or default
        model_name = model_name or os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
        
        self.model_name = model_name
        self.backend = None
        self.model = None
        self.dimension = None
        
        self._initialize()
    
    def _initialize(self):
        """Initialize the embedding backend."""
        # Try Ollama first
        if self._try_ollama():
            return
        
        # Fallback to HuggingFace
        if self.fallback_to_hf and self._try_huggingface():
            return
        
        raise RuntimeError(
            f"Could not initialize embedding model. "
            f"Ensure Ollama is running at {self.ollama_base_url} or install sentence-transformers."
        )
    
    def _try_ollama(self) -> bool:
        """Try to initialize Ollama backend."""
        try:
            import httpx
            
            # Check if Ollama is running
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.ollama_base_url}/api/tags")
                if response.status_code != 200:
                    if self.debug:
                        print(f"[Embedding] Ollama not available: {response.status_code}")
                    return False
                
                # Check if model is available
                models = response.json().get("models", [])
                model_names = [m.get("name", "").split(":")[0] for m in models]
                
                # Handle model name format (with or without tag)
                base_model = self.model_name.split(":")[0]
                if base_model not in model_names and self.model_name not in [m.get("name") for m in models]:
                    if self.debug:
                        print(f"[Embedding] Model {self.model_name} not in Ollama, pulling...")
                    
                    # Try to pull the model
                    try:
                        pull_resp = client.post(
                            f"{self.ollama_base_url}/api/pull",
                            json={"name": self.model_name},
                            timeout=300.0
                        )
                    except:
                        pass
            
            # Set backend
            self.backend = "ollama"
            self.dimension = self.OLLAMA_MODELS.get(self.model_name, 1024)
            
            if self.debug:
                print(f"[Embedding] Using Ollama: {self.model_name} (dim={self.dimension})")
            
            return True
            
        except Exception as e:
            if self.debug:
                print(f"[Embedding] Ollama init failed: {e}")
            return False
    
    def encode_single(self, text: str, is_query: bool = False) -> np.ndarray:
        """Encode a single text."""
        return self.encode([text], is_query=is_query)[0]
    
    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        """
        Encode texts to embeddings.
        
        Args:
            texts: List of texts to encode
            is_query: Whether this is a query (vs document)
            
        Returns:
            numpy array of shape (len(texts), dimension)
        """
        if not texts:
            return np.array([])
        
        if self.backend == "ollama":
            return self._encode_ollama(texts, is_query)
        else:
            return self._encode_huggingface(texts, is_query)
    
    def _encode_ollama(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        """Encode using Ollama API."""
        import httpx
        
        embeddings = []
        
        with httpx.Client(timeout=60.0) as client:
            for text in texts:
                # Add query prefix for some models
                if is_query and "qwen" in self.model_name.lower():
                    text = f"query: {text}"
                
                response = client.post(
                    f"{self.ollama_base_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text}
                )
                
                if response.status_code != 200:
                    raise RuntimeError(f"Ollama embedding failed: {response.text}")
                
                embedding = response.json().get("embedding", [])
                embeddings.append(embedding)
        
        return np.array(embeddings, dtype=np.float32)
    