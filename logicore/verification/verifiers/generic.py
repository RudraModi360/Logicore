"""
Generic verifier.

Fallback verifier that handles ALL artifact types with basic
existence, readability, and format checks.  Other verifiers
extend this with type-specific logic.
"""

from __future__ import annotations

from typing import List, Optional, Set

from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.result import VerificationIssue


# Extensions handled by this verifier — every supported extension.
_ALL_EXTENSIONS: Set[str] = {
    ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".ppsx",
    ".pdf",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg",
    ".html", ".htm",
}

# Magic-byte signatures for common formats.
_MAGIC_SIGNATURES = {
    # PDF
    b"%PDF": ".pdf",
    # PNG
    b"\x89PNG": ".png",
    # JPEG
    b"\xff\xd8\xff": ".jpeg",
    # GIF
    b"GIF8": ".gif",
    # PPTX / DOCX / XLSX (ZIP-based)
    b"PK\x03\x04": ".pptx",
    # BMP
    b"BM": ".bmp",
    # SVG (text-based, check for XML start)
    b"<?xml": ".svg",
    b"<svg": ".svg",
    # HTML (text-based)
    b"<!DOCTYPE": ".html",
    b"<html": ".html",
}


class GenericVerifier(BaseVerifier):
    """Verifier that handles all artifact types with basic checks.

    This is the fallback verifier used when no type-specific verifier
    is registered for a given extension.  It performs generic checks
    only (existence, readability, format validation).
    """

    def supported_extensions(self) -> Set[str]:
        return _ALL_EXTENSIONS

    def _verify_content(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        """Generic content verification: format consistency check."""
        # For the generic verifier we do a lightweight magic-byte check
        # to ensure the file content matches the declared extension.
        self._check_magic_bytes(artifact_path, issues)

    def _check_magic_bytes(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
    ) -> None:
        """Verify file content matches expected format via magic bytes."""
        import os

        ext = os.path.splitext(artifact_path)[1].lower()
        magic = self._read_magic_bytes(artifact_path, n=16)

        if not magic:
            return  # Can't check — empty or unreadable (already caught).

        # Map extension to expected magic prefix.
        ext_to_magic: dict = {
            ".pdf": b"%PDF",
            ".png": b"\x89PNG",
            ".jpeg": b"\xff\xd8\xff",
            ".jpg": b"\xff\xd8\xff",
            ".gif": b"GIF8",
            ".bmp": b"BM",
            ".pptx": b"PK\x03\x04",
            ".docx": b"PK\x03\x04",
            ".xlsx": b"PK\x03\x04",
            ".ppsx": b"PK\x03\x04",
        }

        expected = ext_to_magic.get(ext)
        if expected and not magic.startswith(expected):
            self._add_issue(
                issues,
                severity="warning",
                category="format",
                description=(
                    f"File content does not look like a valid {ext} file "
                    f"(magic bytes do not match expected format)"
                ),
                auto_fixable=False,
            )
