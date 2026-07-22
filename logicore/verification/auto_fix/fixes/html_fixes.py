"""
HTML auto-fixes.

Handles fixing common HTML issues:
- Add DOCTYPE declaration
- Add viewport meta tag
- Add title tag
- Add alt text to images
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logicore.verification.auto_fix import AutoFixEngine

logger = logging.getLogger(__name__)


def register_fixes(engine: "AutoFixEngine") -> None:
    """Register HTML fix handlers with the auto-fix engine."""
    engine.register_fix("structure", "missing doctype", _fix_add_doctype)
    engine.register_fix("structure", "missing viewport", _fix_add_viewport)
    engine.register_fix("structure", "missing title", _fix_add_title)
    engine.register_fix("content", "missing alt text", _fix_add_alt_text)
    engine.register_fix("content", "empty container", _fix_empty_containers)


def _fix_add_doctype(artifact_path: str, issue) -> bool:
    """Add DOCTYPE declaration to HTML file."""
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check if DOCTYPE already exists.
        if content.lower().startswith("<!doctype"):
            return False

        # Add DOCTYPE at the beginning.
        new_content = "<!DOCTYPE html>\n" + content

        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to add DOCTYPE: {exc}")
        return False


def _fix_add_viewport(artifact_path: str, issue) -> bool:
    """Add viewport meta tag to HTML head."""
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check if viewport already exists.
        if "viewport" in content.lower():
            return False

        viewport_tag = '<meta name="viewport" content="width=device-width, initial-scale=1">'

        # Try to insert after <head> or <title>.
        head_match = re.search(r"<head[^>]*>", content, re.I)
        if head_match:
            insert_pos = head_match.end()
            new_content = content[:insert_pos] + "\n" + viewport_tag + content[insert_pos:]
        else:
            # Insert after <html> tag.
            html_match = re.search(r"<html[^>]*>", content, re.I)
            if html_match:
                insert_pos = html_match.end()
                new_content = content[:insert_pos] + "\n<head>\n" + viewport_tag + "\n</head>" + content[insert_pos:]
            else:
                # Prepend.
                new_content = viewport_tag + "\n" + content

        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to add viewport: {exc}")
        return False


def _fix_add_title(artifact_path: str, issue) -> bool:
    """Add empty title tag to HTML head."""
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check if title already exists.
        if re.search(r"<title[^>]*>.*?</title>", content, re.I | re.S):
            return False

        title_tag = "<title>Document</title>"

        # Try to insert after <head>.
        head_match = re.search(r"<head[^>]*>", content, re.I)
        if head_match:
            insert_pos = head_match.end()
            new_content = content[:insert_pos] + "\n" + title_tag + content[insert_pos:]
        else:
            return False

        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to add title: {exc}")
        return False


def _fix_add_alt_text(artifact_path: str, issue) -> bool:
    """Add alt text to images missing it."""
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find images without alt text.
        img_pattern = re.compile(r"<img([^>]*?)>", re.I)
        modified = False

        def add_alt(match):
            nonlocal modified
            attrs = match.group(1)

            # Check if alt exists.
            if re.search(r'alt\s*=', attrs, re.I):
                return match.group(0)

            # Add alt attribute.
            modified = True
            return f'<img{attrs} alt="Image">'

        new_content = img_pattern.sub(add_alt, content)

        if not modified:
            return False

        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to add alt text: {exc}")
        return False


def _fix_empty_containers(artifact_path: str, issue) -> bool:
    """Add placeholder comment to empty containers."""
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find empty divs/sections.
        container_pattern = re.compile(r"<(div|section|article|main)(\s[^>]*)?>\s*</\1>", re.I)
        modified = False

        def add_placeholder(match):
            nonlocal modified
            modified = True
            tag = match.group(1)
            attrs = match.group(2) or ""
            return f"<{tag}{attrs}>\n  <!-- Content needed -->\n</{tag}>"

        new_content = container_pattern.sub(add_placeholder, content)

        if not modified:
            return False

        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True

    except Exception as exc:
        logger.debug(f"Failed to fix empty containers: {exc}")
        return False
