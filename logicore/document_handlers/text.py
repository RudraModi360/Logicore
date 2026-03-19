from typing import Dict, Any
from .base import BaseDocumentHandler

class TextHandler(BaseDocumentHandler):
    """Handler for plain text files (.txt, .md, .py, etc.)."""

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._text = ""
        self._metadata = {}

    def load(self) -> None:
        """Load the text file."""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                self._text = f.read()
            
            # Minimal metadata
            self._metadata = {
                "size_bytes": len(self._text),
                "encoding": "utf-8"
            }
        except Exception as e:
            raise RuntimeError(f"Failed to load text file {self.file_path}: {e}")

    def get_text(self) -> str:
        if not self._text:
            self.load()
        return self._text

    def get_metadata(self) -> Dict[str, Any]:
        if not self._metadata:
            self.load()
        return self._metadata
