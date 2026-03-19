import os
from io import BytesIO

try:
    from ollama import Client
    from PIL import Image
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

class OllamaVisionService:
    _client = None
    _model = "qwen3-vl:2b"
    
    @classmethod
    def _get_client(cls):
        if not OLLAMA_AVAILABLE:
            raise RuntimeError("ollama library is not installed.")
        
        if cls._client is None:
            # Assumes local ollama instance
            cls._client = Client(host='http://localhost:11434')
        return cls._client

    @classmethod
    def get_text_from_image(cls, image_source: str) -> str:
        """
        Extract text from an image using a local Ollama vision model.
        image_source: Path to image file
        """
        return cls._process_image(image_source, "Extract all text present in this image directly. Output ONLY the extracted text, no introspection or markdown formatting.")

    @classmethod
    def get_text_from_pil_image(cls, image: 'Image.Image') -> str:
        """
        Extract text from a PIL Image object.
        """
        # Convert PIL to bytes
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_bytes = buffered.getvalue()
        
        return cls._process_image(img_bytes, "Extract all text present in this image directly. Output ONLY the extracted text, no introspection or markdown formatting.")

    @classmethod
    def _process_image(cls, image_data, prompt: str) -> str:
        try:
            client = cls._get_client()
            response = client.chat(
                model=cls._model,
                messages=[{
                    'role': 'user',
                    'content': prompt,
                    'images': [image_data]
                }]
            )
            return response['message']['content'].strip()
        except Exception as e:
            return f"[Ollama Vision Failed: {e}]"
