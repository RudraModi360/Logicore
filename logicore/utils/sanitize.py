"""Filesystem-safe name sanitization shared by context services."""

from __future__ import annotations

import re


def sanitize_fs_component(name: str, max_len: int = 50) -> str:
    """Sanitize a component used in filesystem paths.

    Removes path separators and special characters, collapses repeated
    underscores, strips leading/trailing underscores, and caps length.
    """
    sanitized = re.sub(r"[^\w\-]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:max_len]
