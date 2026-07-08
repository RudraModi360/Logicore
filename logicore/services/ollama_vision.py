"""
OllamaVisionService: VLM-powered image understanding via local Ollama.

Uses gemma3:4b-cloud for:
- Fast OCR text extraction from images
- Reasoning about image content with custom prompts
- Page-level analysis of scanned documents

The main agent can pass its own reasoning prompts to reason_about_image()
so the VLM response is directly useful for the agent's task.
"""

from io import BytesIO
import logging

logger = logging.getLogger(__name__)

try:
    from ollama import Client
    from PIL import Image
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class OllamaVisionService:
    """VLM service for image OCR and reasoning via local Ollama."""

    _client = None
    _model = "gemma3:4b-cloud"

    @classmethod
    def _get_client(cls):
        if not OLLAMA_AVAILABLE:
            raise RuntimeError("ollama library is not installed.")

        if cls._client is None:
            cls._client = Client(host='http://localhost:11434', timeout=60)
        return cls._client

    # ── OCR: Extract raw text from image ────────────────────────────

    @classmethod
    def get_text_from_image(cls, image_source: str) -> str:
        """
        Extract all visible text from an image file.
        Returns raw extracted text, no markdown formatting.
        """
        return cls._process_image(
            image_source,
            "Extract all visible text from this image. Output ONLY the extracted text, "
            "preserving line breaks. No commentary, no markdown."
        )

    @classmethod
    def get_text_from_pil_image(cls, image: 'Image.Image') -> str:
        """
        Extract all visible text from a PIL Image object.
        Returns raw extracted text.
        """
        img_bytes = cls._pil_to_bytes(image)
        return cls._process_image(
            img_bytes,
            "Extract all visible text from this image. Output ONLY the extracted text, "
            "preserving line breaks. No commentary, no markdown."
        )

    # ── Reasoning: VLM with custom prompt ───────────────────────────

    @classmethod
    def reason_about_image(cls, image: 'Image.Image', prompt: str) -> str:
        """
        Send an image + a custom reasoning prompt to the VLM.
        The main agent generates the prompt based on its task context.

        Args:
            image: PIL Image to analyze
            prompt: Reasoning prompt from the agent (e.g. "Summarize this page",
                    "Extract all tables", "What are the key findings?")

        Returns:
            VLM response text
        """
        img_bytes = cls._pil_to_bytes(image)
        return cls._process_image(img_bytes, prompt)

    @classmethod
    def reason_about_image_bytes(cls, image_bytes: bytes, prompt: str) -> str:
        """
        Send raw image bytes + a custom reasoning prompt to the VLM.
        """
        return cls._process_image(image_bytes, prompt)

    # ── Internal ────────────────────────────────────────────────────

    @classmethod
    def _pil_to_bytes(cls, image: 'Image.Image') -> bytes:
        """Convert PIL Image to PNG bytes."""
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        return buffered.getvalue()

    @classmethod
    def _process_image(cls, image_data, prompt: str) -> str:
        """
        Core VLM call. Sends image + prompt to gemma3:4b-cloud via Ollama.
        Returns response text or error string.
        """
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
            logger.warning(f"Ollama vision call failed: {e}")
            return f"[Vision Failed: {e}]"
