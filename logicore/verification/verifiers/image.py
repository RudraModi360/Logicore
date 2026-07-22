"""
Image verifier.

Verifies image files: dimensions, format, metadata, corruption.
Supports JPG, PNG, GIF, WEBP, BMP, TIFF, SVG.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set

from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.result import VerificationIssue


# Supported image extensions.
IMAGE_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg",
}

# Minimum reasonable image dimensions.
MIN_DIMENSION = 10

# Maximum reasonable image dimensions (100 megapixels).
MAX_PIXELS = 100_000_000

# Maximum file size for images (500 MB).
MAX_FILE_SIZE = 500 * 1024 * 1024


class ImageVerifier(BaseVerifier):
    """Verify image files: dimensions, format, metadata, corruption.

    Checks:
    - File is a valid image that can be opened
    - Dimensions are reasonable (not too small, not too large)
    - Image is not corrupted (can be fully loaded)
    - Format matches file extension
    - SVG files have valid XML structure
    """

    def supported_extensions(self) -> Set[str]:
        return IMAGE_EXTENSIONS

    def _verify_content(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        ext = os.path.splitext(artifact_path)[1].lower()

        if ext == ".svg":
            self._verify_svg(artifact_path, issues)
        else:
            self._verify_raster(artifact_path, ext, issues)

    def _verify_raster(
        self,
        artifact_path: str,
        ext: str,
        issues: List[VerificationIssue],
    ) -> None:
        """Verify raster images (JPG, PNG, GIF, WEBP, BMP, TIFF)."""
        try:
            from PIL import Image
        except ImportError:
            # Pillow not available — skip content checks but note it.
            self._add_issue(
                issues,
                severity="info",
                category="dependency",
                description="Pillow library not installed — skipping detailed image verification",
            )
            return

        try:
            img = Image.open(artifact_path)
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Cannot open image: {exc}",
                auto_fixable=False,
            )
            return

        # Check if image can be fully loaded (catches truncated/corrupted files).
        try:
            img.load()
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Image is corrupted or truncated: {exc}",
                auto_fixable=False,
            )
            return

        # Dimensions check.
        width, height = img.size
        if width < MIN_DIMENSION or height < MIN_DIMENSION:
            self._add_issue(
                issues,
                severity="warning",
                category="content",
                description=f"Image is very small ({width}x{height})",
                auto_fixable=False,
                fix_suggestion="Consider using a larger image for better quality",
            )

        if width * height > MAX_PIXELS:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description=f"Image is very large ({width}x{height}, {width*height // 1_000_000}MP)",
                auto_fixable=False,
                fix_suggestion="Consider reducing image dimensions",
            )

        # Format consistency check.
        expected_format = {
            ".jpg": "JPEG", ".jpeg": "JPEG",
            ".png": "PNG",
            ".gif": "GIF",
            ".webp": "WEBP",
            ".bmp": "BMP",
            ".tiff": "TIFF", ".tif": "TIFF",
        }
        expected = expected_format.get(ext)
        if expected and img.format and img.format.upper() != expected.upper():
            self._add_issue(
                issues,
                severity="warning",
                category="format",
                description=f"File extension is {ext} but image format is {img.format}",
                auto_fixable=False,
                fix_suggestion="Rename file to match actual format",
            )

        # Color mode check.
        if img.mode == "P" and img.info.get("transparency") is not None:
            pass  # Palette with transparency is fine.
        elif img.mode not in ("RGB", "RGBA", "L", "LA", "P", "CMYK", "1"):
            self._add_issue(
                issues,
                severity="info",
                category="format",
                description=f"Unusual color mode: {img.mode}",
            )

    def _verify_svg(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
    ) -> None:
        """Verify SVG files have valid XML structure."""
        try:
            import xml.etree.ElementTree as ET
        except ImportError:
            return

        try:
            tree = ET.parse(artifact_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"SVG has invalid XML: {exc}",
                auto_fixable=False,
            )
            return
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Cannot parse SVG: {exc}",
                auto_fixable=False,
            )
            return

        # Check root element is <svg>.
        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        if tag.lower() != "svg":
            self._add_issue(
                issues,
                severity="critical",
                category="format",
                description=f"Root element is <{tag}>, expected <svg>",
                auto_fixable=False,
            )
            return

        # Check for viewBox or width/height.
        has_viewbox = "viewBox" in root.attrib
        has_size = "width" in root.attrib and "height" in root.attrib
        if not has_viewbox and not has_size:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description="SVG has no viewBox or width/height attributes",
                auto_fixable=False,
                fix_suggestion="Add viewBox attribute for proper scaling",
            )

        # Check for child elements (empty SVG).
        if len(root) == 0:
            self._add_issue(
                issues,
                severity="warning",
                category="content",
                description="SVG has no child elements (empty)",
                auto_fixable=False,
            )


def get_verifier() -> ImageVerifier:
    """Return an ImageVerifier instance for registry auto-discovery."""
    return ImageVerifier()
