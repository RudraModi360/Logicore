from typing import Dict, Any, Optional
from .base import BaseDocumentHandler

# Maximum pages to OCR via VLM before giving up (prevents hanging on large scanned PDFs)
_MAX_OCR_PAGES = 10


class PDFHandler(BaseDocumentHandler):
    """Handler for PDF documents with hybrid OCR (pytesseract primary, Ollama VLM fallback)."""

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._reader = None
        self._text = ""
        self._metadata = {}

    def load(self, reasoning_prompt: Optional[str] = None) -> None:
        """
        Load and parse the PDF file.

        Args:
            reasoning_prompt: If provided, scanned pages are sent to the VLM with
                            this prompt instead of raw OCR. Useful when the agent
                            wants targeted analysis (e.g. "summarize this page").
        """
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
            self._reader = pypdf.PdfReader(self.file_path)
            reader = self._reader

            # Check if OCR service is available
            try:
                from ..services.ocr_service import ocr_from_image, reason_about_image
                from PIL import Image
                from io import BytesIO
                OCR_AVAILABLE = True
            except ImportError:
                OCR_AVAILABLE = False

            ocr_count = 0
            for i, doc in enumerate(documents):
                page_text = doc.page_content

                # Check if page is likely scanned (has images, little text)
                is_scanned = False
                try:
                    if i < len(reader.pages):
                        page = reader.pages[i]
                        if len(page.images) > 0 and len(page_text.strip()) < 50:
                            is_scanned = True
                except (IndexError, AttributeError):
                    pass

                # If scanned and OCR available, process images
                if is_scanned and OCR_AVAILABLE and ocr_count < _MAX_OCR_PAGES:
                    image_texts = []
                    try:
                        for img_obj in reader.pages[i].images:
                            try:
                                image = Image.open(BytesIO(img_obj.data))

                                # Use reasoning prompt if provided, otherwise raw OCR
                                if reasoning_prompt:
                                    ocr_text = reason_about_image(image, reasoning_prompt)
                                else:
                                    ocr_text = ocr_from_image(image)

                                if ocr_text.strip():
                                    image_texts.append(ocr_text)
                            except Exception:
                                continue
                    except (IndexError, AttributeError):
                        pass

                    if image_texts:
                        combined = "\n".join(image_texts)
                        engine = "vlm_reasoning" if reasoning_prompt else "ocr"
                        final_text_parts.append(f"--- Page {i+1} ({engine}) ---\n{combined}")
                        self._metadata[f"page_{i+1}_engine"] = engine
                        ocr_count += 1
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
        if not self._text:
            self.load()
        return self._text

    def get_metadata(self) -> Dict[str, Any]:
        if not self._metadata:
            self.load()
        return self._metadata
