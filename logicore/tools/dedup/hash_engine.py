"""
Centralized content hashing utilities.

Provides fast, deterministic hashing for tool call signatures,
content change detection, and pair disambiguation.
"""

import hashlib
import json
from typing import Any, Dict, Optional


def hash_content(content: str) -> str:
    """
    Fast content hash using blake2b (100x faster than SHA-256).
    Falls back to SHA-256 if blake2b is unavailable.
    """
    encoded = content.encode("utf-8")
    try:
        return hashlib.blake2b(encoded, digest_size=16).hexdigest()
    except (ValueError, AttributeError):
        return hashlib.sha256(encoded).hexdigest()


def hash_tool_call(name: str, args: Dict[str, Any]) -> str:
    """
    Create a stable SHA-256 signature for a tool call.
    Used as cache key for deduplication.
    """
    try:
        args_json = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        args_json = str(args)
    key = f"{name}:{args_json}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hash_pair(a: str, b: str) -> str:
    """
    Hash two strings together without concatenation.
    Uses seed-chained hashing to avoid collision ambiguity.
    """
    h1 = hashlib.blake2b(a.encode("utf-8"), digest_size=8).digest()
    h2 = hashlib.blake2b(b.encode("utf-8"), digest_size=8, key=h1).digest()
    return (h1 + h2).hex()
