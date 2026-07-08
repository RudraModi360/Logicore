"""
ToolOutputDistiller: Per-result truncation for tool outputs.

Extracted from Agent._serialize_tool_result_for_model().
Enforces per-tool-call size limits before results enter the message history.
"""

from __future__ import annotations

import json
from typing import Dict, Any


# Default limits — reduced from 12K/4K to prevent large file reads from
# flooding message history and causing context window exhaustion.
DEFAULT_MAX_CHARS = 6000
DEFAULT_PREVIEW_CHARS = 2000


class ToolOutputDistiller:
    """
    Truncates tool outputs to keep them within context budget.

    Applied per-tool-call before the result is added to session.messages.
    This is different from ToolOutputMaskingService which operates on
    the full message history before LLM calls.
    """

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
    ):
        self.max_chars = max_chars
        self.preview_chars = preview_chars

    def distill(
        self,
        tool_name: str,
        result: Dict[str, Any],
        reused: bool = False,
    ) -> str:
        """
        Serialize and optionally truncate a tool result for the model.

        Args:
            tool_name: Name of the tool
            result: Tool execution result dict
            reused: Whether this result was from cache

        Returns:
            JSON string ready for the tool message content field
        """
        payload = {
            "tool": tool_name,
            "success": bool(result.get("success", False)),
            "reused_cached_result": reused,
        }

        if result.get("error"):
            payload["error"] = str(result.get("error"))

        if "content" in result:
            payload["content"] = result.get("content")

        serialized = json.dumps(payload, ensure_ascii=False)

        if len(serialized) <= self.max_chars:
            return serialized

        # Truncate: keep preview + metadata
        content_preview = str(result.get("content", ""))[: self.preview_chars]
        compact_payload = {
            "tool": tool_name,
            "success": bool(result.get("success", False)),
            "reused_cached_result": reused,
            "error": str(result.get("error")) if result.get("error") else None,
            "content_preview": content_preview,
            "_truncated": True,
            "_note": "Tool output truncated for context efficiency. Use this result before recalling the same tool with identical arguments.",
        }
        return json.dumps(compact_payload, ensure_ascii=False)
