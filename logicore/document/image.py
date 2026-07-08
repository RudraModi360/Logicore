from typing import Dict, Any
from .base import BaseDocumentHandler


class ImageHandler(BaseDocumentHandler):
    """Handler for Image files (PNG, JPG, WEBP) with hybrid OCR."""

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._text = ""
        self._metadata = {}

    def load(self) -> None:
        """Load and parse the image file using hybrid OCR (pytesseract + VLM fallback)."""
        # Extract basic image metadata first
        try:
            from PIL import Image
            with Image.open(self.file_path) as img:
                self._metadata = {
                    "format": img.format,
                    "mode": img.mode,
                    "width": img.width,
                    "height": img.height,
                    "size_bytes": __import__('os').path.getsize(self.file_path),
                }
        except ImportError:
            self._metadata = {
                "size_bytes": __import__('os').path.getsize(self.file_path),
            }
        except Exception:
            pass

        # Try hybrid OCR (pytesseract primary, VLM fallback)
        try:
            from ..services.ocr_service import ocr_from_file
            self._text = ocr_from_file(self.file_path)
            self._metadata["ocr_engine"] = "hybrid_ocr"
        except ImportError:
            self._text = "[OCR service not available]"
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
