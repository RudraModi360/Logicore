"""
JSON helpers for storage backends.

Agent session messages can contain provider-specific objects
(e.g. ``ToolCall`` from OpenAI/Ollama SDKs, Pydantic models, dataclasses)
that the standard ``json.dumps`` cannot serialize. These helpers
recursively convert any object into JSON-native structures so sessions
always persist, and reload losslessly as plain dicts/lists.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, date
from typing import Any
from uuid import UUID


def _to_json_safe(obj: Any) -> Any:
    """Recursively convert an arbitrary object into a JSON-safe structure."""
    # Primitives pass through
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Common stdlib types
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, set):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_json_safe(v) for v in obj]

    # Dict-like
    if isinstance(obj, dict):
        return {str(k): _to_json_safe(v) for k, v in obj.items()}

    # List / tuple-like
    if isinstance(obj, (list,)):
        return [_to_json_safe(v) for v in obj]

    # Dataclass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_json_safe(dataclasses.asdict(obj))

    # Pydantic v2
    try:
        if hasattr(obj, "model_dump"):
            return _to_json_safe(obj.model_dump())
    except Exception:
        pass

    # Pydantic v1
    try:
        if hasattr(obj, "dict"):
            return _to_json_safe(obj.dict())
    except Exception:
        pass

    # Anything else: try __dict__, then str() as last resort
    if hasattr(obj, "__dict__"):
        return _to_json_safe(
            {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        )

    return str(obj)


def dumps(obj: Any, **kwargs: Any) -> str:
    """JSON-serialize anything, converting non-native objects safely."""
    return json.dumps(_to_json_safe(obj), **kwargs)


def loads(s: str) -> Any:
    """JSON-deserialize (thin wrapper for symmetry)."""
    return json.loads(s)
