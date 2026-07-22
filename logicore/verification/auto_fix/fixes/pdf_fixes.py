"""
PDF auto-fixes.

Handles fixing common PDF issues:
- Add missing EOF marker
- Repair truncated PDFs
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logicore.verification.auto_fix import AutoFixEngine

logger = logging.getLogger(__name__)


def register_fixes(engine: "AutoFixEngine") -> None:
    """Register PDF fix handlers with the auto-fix engine."""
    engine.register_fix("corruption", "truncated", _fix_truncated_pdf)
    engine.register_fix("format", "does not have a valid pdf header", _fix_invalid_header)


def _fix_truncated_pdf(artifact_path: str, issue) -> bool:
    """Add missing EOF marker to truncated PDFs."""
    try:
        # Read the file.
        with open(artifact_path, "rb") as f:
            content = f.read()

        # Check if EOF marker exists.
        if b"%%EOF" in content:
            return False

        # Add EOF marker.
        # PDF spec requires newline before %%EOF.
        if not content.endswith(b"\n"):
            content += b"\n"
        content += b"%%EOF\n"

        # Write back.
        with open(artifact_path, "wb") as f:
            f.write(content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to fix truncated PDF: {exc}")
        return False


def _fix_invalid_header(artifact_path: str, issue) -> bool:
    """Try to repair PDF with invalid header by adding proper header."""
    try:
        with open(artifact_path, "rb") as f:
            content = f.read()

        # Check if content looks like PDF data.
        if b"obj" not in content and b"stream" not in content:
            return False  # Not PDF data at all.

        # Add proper PDF header.
        new_content = b"%PDF-1.4\n" + content

        with open(artifact_path, "wb") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to fix PDF header: {exc}")
        return False
