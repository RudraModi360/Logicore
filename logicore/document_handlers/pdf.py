from typing import Dict, Any
from .base import BaseDocumentHandler

class PDFHandler(BaseDocumentHandler):
    """Handler for PDF documents using PyPDFLoader with OllamaVision fallback for scanned pages."""

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._reader = None
        self._text = ""
        self._metadata = {}

    def load(self) -> None:
        """Load and parse the PDF file using PyPDFLoader with OllamaVision fallback for scanned pages."""
        try:
            from langchain_community.document_loaders import PyPDFLoader
            import pypdf
        except ImportError:
            raise RuntimeError("langchain_community or pypdf is not installed.")

        try:
            # 1. Load Text via PyPDFLoader
            loader = PyPDFLoader(self.file_path)
            documents = loader.load()
            
            final_text_parts = []
            reader = pypdf.PdfReader(self.file_path)
            
            # Check if Ollama Vision is available
            try:
                from ..services.ollama_vision import OllamaVisionService
                from PIL import Image
                from io import BytesIO
                VISION_AVAILABLE = True
            except ImportError:
                VISION_AVAILABLE = False
            
            for i, doc in enumerate(documents):
                page_text = doc.page_content
                
                # Check if page is likely scanned (has images, little text)
                is_scanned = False
                try:
                    if i < len(reader.pages):
                        page = reader.pages[i]
                        if len(page.images) > 0 and len(page_text.strip()) < 50:
                            is_scanned = True
                except:
                    pass
                
                # If scanned and Vision available, OCR the images
                if is_scanned and VISION_AVAILABLE:
                    image_texts = []
                    try:
                        for img_obj in reader.pages[i].images:
                            try:
                                image = Image.open(BytesIO(img_obj.data))
                                ocr_text = OllamaVisionService.get_text_from_pil_image(image)
                                if ocr_text.strip():
                                    image_texts.append(ocr_text)
                            except:
                                continue
                    except:
                        pass
                    
                    if image_texts:
                        combined = "\n".join(image_texts)
                        final_text_parts.append(f"--- Page {i+1} (OllamaVision) ---\n{combined}")
                        self._metadata[f"page_{i+1}_engine"] = "ollama_vision"
                        continue
                
                # Standard text
                final_text_parts.append(page_text)
            
            self._text = "\n\n".join(final_text_parts).strip()
            
            # Extract metadata
            if reader.metadata:
                for key, value in reader.metadata.items():
                    clean_key = key[1:] if key.startswith('/') else key
                    self._metadata[clean_key] = value
                    
        except Exception as e:
            raise RuntimeError(f"Failed to load PDF file {self.file_path}: {e}")

    def get_text(self) -> str:
        if self._reader is None:
            self.load()
        return self._text

    def get_metadata(self) -> Dict[str, Any]:
        if self._reader is None:
            self.load()
        return self._metadata
