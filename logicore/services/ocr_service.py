"""
OCRService: Hybrid OCR with pytesseract primary + Ollama VLM fallback.

Strategy:
  1. Try pytesseract (fast, no GPU, no network) — works for most printed text
  2. If pytesseract produces < MIN_TEXT_LENGTH chars, fall back to Ollama VLM
  3. For reasoning tasks (summarize, extract tables, etc.), use VLM directly

The main agent can pass reasoning prompts via reason_about_image() so the
VLM response is directly useful for its task context.
"""

import logging
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Minimum characters pytesseract must produce before we consider it successful.
# If pytesseract returns fewer chars than this, we assume the image is scanned/low-quality
# and fall back to the VLM.
MIN_TEXT_LENGTH = 20

# Maximum pages to process with VLM fallback (prevents hanging on large scanned docs)
MAX_VLM_PAGES = 10


# ── pytesseract availability ────────────────────────────────────────

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.debug("pytesseract not installed — VLM fallback will be used for all OCR")


# ── Core OCR ────────────────────────────────────────────────────────

def ocr_from_image(image: Image.Image) -> str:
    """
    Extract text from a PIL Image.
    Tries pytesseract first; falls back to Ollama VLM if result is poor.
    """
    # 1. Fast path: pytesseract
    if TESSERACT_AVAILABLE:
        try:
            text = pytesseract.image_to_string(image, timeout=15)
            text = text.strip()
            if len(text) >= MIN_TEXT_LENGTH:
                return text
            logger.debug(f"pytesseract produced only {len(text)} chars, trying VLM fallback")
        except Exception as e:
            logger.debug(f"pytesseract failed: {e}, trying VLM fallback")

    # 2. Fallback: Ollama VLM (gemma3:4b-cloud)
    return _vlm_ocr(image)


def ocr_from_image_bytes(image_bytes: bytes) -> str:
    """OCR from raw image bytes."""
    try:
        image = Image.open(__import__('io').BytesIO(image_bytes))
        return ocr_from_image(image)
    except Exception as e:
        return f"[OCR Failed: {e}]"


def ocr_from_file(file_path: str) -> str:
    """OCR from an image file path."""
    try:
        with Image.open(file_path) as img:
            return ocr_from_image(img)
    except Exception as e:
        return f"[OCR Failed: {e}]"


# ── VLM-powered reasoning ──────────────────────────────────────────

def reason_about_image(image: Image.Image, prompt: str) -> str:
    """
    Send an image + a custom reasoning prompt to the VLM.
    The main agent generates the prompt based on its task context.

    Examples:
        reason_about_image(img, "Summarize the key findings on this page")
        reason_about_image(img, "Extract all tables as markdown")
        reason_about_image(img, "What are the action items mentioned?")

    Returns:
        VLM response text
    """
    try:
        from .ollama_vision import OllamaVisionService
        return OllamaVisionService.reason_about_image(image, prompt)
    except Exception as e:
        return f"[Vision reasoning failed: {e}]"


def reason_about_image_bytes(image_bytes: bytes, prompt: str) -> str:
    """Reason about raw image bytes with a custom prompt."""
    try:
        image = Image.open(__import__('io').BytesIO(image_bytes))
        return reason_about_image(image, prompt)
    except Exception as e:
        return f"[Vision reasoning failed: {e}]"


# ── Batch page processing ──────────────────────────────────────────

def ocr_pages(pages: list, reasoning_prompt: Optional[str] = None) -> list:
    """
    Process a list of PIL Images (PDF pages) with OCR.

    Args:
        pages: List of PIL Images
        reasoning_prompt: If provided, each page is sent to VLM with this prompt
                         instead of raw OCR. Useful for "summarize this page" type tasks.

    Returns:
        List of (page_index, text) tuples
    """
    results = []
    vlm_count = 0

    for i, page_img in enumerate(pages):
        if reasoning_prompt and vlm_count < MAX_VLM_PAGES:
            text = reason_about_image(page_img, reasoning_prompt)
            vlm_count += 1
        else:
            text = ocr_from_image(page_img)
        results.append((i, text))

    return results


# ── Internal ────────────────────────────────────────────────────────

def _vlm_ocr(image: Image.Image) -> str:
    """Fallback OCR using Ollama VLM."""
    try:
        from .ollama_vision import OllamaVisionService
        return OllamaVisionService.get_text_from_pil_image(image)
    except Exception as e:
        logger.warning(f"VLM OCR fallback failed: {e}")
        return f"[OCR Failed: pytesseract unavailable and VLM fallback failed: {e}]"
