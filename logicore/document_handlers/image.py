from typing import Dict, Any
from .base import BaseDocumentHandler

class ImageHandler(BaseDocumentHandler):
    """Handler for Image files (PNG, JPG, WEBP) using Ollama Vision."""

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._text = ""
        self._metadata = {}

    def load(self) -> None:
        """Load and parse the image file using Ollama Vision."""
        try:
            from ..services.ollama_vision import OllamaVisionService
            if OllamaVisionService: 
                pass
        except ImportError:
            self._text = "[Ollama Vision service not available]"
            return

        try:
            self._text = OllamaVisionService.get_text_from_image(self.file_path)
            self._metadata["ocr_engine"] = "OllamaVision"
        except Exception as e:
            self._text = f"[OCR Failed: {e}]"

    def get_text(self) -> str:
        if not self._text:
            self.load()
        return self._text

    def get_metadata(self) -> Dict[str, Any]:
         if not self._metadata:
             self.load()
         return self._metadata
