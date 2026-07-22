"""
Image auto-fixes.

Handles fixing common image issues:
- Resize overly large images
- Convert color modes
- Fix SVG viewBox
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logicore.verification.auto_fix import AutoFixEngine

logger = logging.getLogger(__name__)


def register_fixes(engine: "AutoFixEngine") -> None:
    """Register image fix handlers with the auto-fix engine."""
    engine.register_fix("content", "image is very small", _fix_small_image)
    engine.register_fix("structure", "image is very large", _fix_large_image)
    engine.register_fix("format", "unusual color mode", _fix_color_mode)
    engine.register_fix("structure", "no viewBox", _fix_svg_viewbox)


def _fix_small_image(artifact_path: str, issue) -> bool:
    """Resize overly small images to a minimum size."""
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        img = Image.open(artifact_path)
        width, height = img.size

        # Minimum 100x100 for usability.
        new_width = max(width, 100)
        new_height = max(height, 100)

        if new_width == width and new_height == height:
            return False  # No change needed.

        # Resize with high-quality resampling.
        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save back to same format.
        img_resized.save(artifact_path, quality=95)
        return True

    except Exception as exc:
        logger.debug(f"Failed to fix small image: {exc}")
        return False


def _fix_large_image(artifact_path: str, issue) -> bool:
    """Resize overly large images to maximum 4000x4000."""
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        img = Image.open(artifact_path)
        width, height = img.size

        max_dim = 4000
        if width <= max_dim and height <= max_dim:
            return False  # No change needed.

        # Calculate new dimensions maintaining aspect ratio.
        ratio = min(max_dim / width, max_dim / height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)

        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img_resized.save(artifact_path, quality=90)
        return True

    except Exception as exc:
        logger.debug(f"Failed to fix large image: {exc}")
        return False


def _fix_color_mode(artifact_path: str, issue) -> bool:
    """Convert unusual color modes to RGB/RGBA."""
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        img = Image.open(artifact_path)

        # Only fix unusual modes.
        unusual_modes = {"CMYK", "LAB", "HSV", "I", "F"}
        if img.mode not in unusual_modes:
            return False

        # Convert to RGB (drop alpha if present).
        if img.mode in ("CMYK", "LAB", "HSV"):
            img_rgb = img.convert("RGB")
            img_rgb.save(artifact_path, quality=95)
            return True

        return False

    except Exception as exc:
        logger.debug(f"Failed to fix color mode: {exc}")
        return False


def _fix_svg_viewbox(artifact_path: str, issue) -> bool:
    """Add viewBox attribute to SVG if missing."""
    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        return False

    try:
        tree = ET.parse(artifact_path)
        root = tree.getroot()

        # Check if viewBox already exists.
        if "viewBox" in root.attrib:
            return False

        # Get width and height.
        width = root.get("width", "100")
        height = root.get("height", "100")

        # Clean values (remove px, pt, etc.).
        def clean_dim(val):
            import re
            match = re.match(r"([\d.]+)", val)
            return match.group(1) if match else "100"

        w = clean_dim(width)
        h = clean_dim(height)

        # Add viewBox.
        root.set("viewBox", f"0 0 {w} {h}")

        # Write back.
        tree.write(artifact_path, xml_declaration=True, encoding="UTF-8")
        return True

    except Exception as exc:
        logger.debug(f"Failed to fix SVG viewBox: {exc}")
        return False
