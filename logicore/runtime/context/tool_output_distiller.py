"""
ToolOutputDistiller: window-scaled tool-output distillation with file offload.

Insertion-time stage: applied per-tool-call before results enter message
history. Complements ToolOutputMaskingService (history backstop stage) which
operates on full history before LLM calls. The two stages keep separate
exempt sets by design:

* Distiller exempts read_file (and interactive tools): the agent just fetched
  this output and must see it in full; masking may offload it later.
* Masking deliberately does NOT exempt read_file: old read_file outputs are
  exactly what masking must offload, and masking's stub points at a saved file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional, Set

from logicore.utils.sanitize import sanitize_fs_component

DISTILLED_INDICATOR = "[OUTPUT_DISTILLED]"
DISTILLED_DIR = "logicore_distilled"


class ToolOutputDistiller:
    """
    Distills (or offloads) tool outputs so oversized results never enter
    message history unretrievably.

    Outputs at or below the window-scaled token threshold pass through
    unchanged. Larger outputs from non-exempt tools are saved to disk and
    replaced by a [OUTPUT_DISTILLED] stub carrying an excerpt and a
    read_file(path) retrieval instruction, so nothing is ever lost.
    """

    def __init__(
        self,
        context_cfg: Any,
        model_window: int,
        estimator: Any,
        session_root: Optional[str] = None,
    ):
        """
        Args:
            context_cfg: RuntimeContextConfig (reads distillation_threshold_tokens
                and distill_exempt_tools).
            model_window: Resolved model context window in tokens.
            estimator: TokenEstimator (single token truth).
            session_root: Root directory for offloaded outputs. Defaults to
                the OS temp dir under logicore_distilled/.
        """
        self._window = model_window
        self._estimator = estimator
        self.soft_threshold = min(
            context_cfg.distillation_threshold_tokens,
            max(500, model_window // 10),
        )
        # Excerpt length scales with window: on an 8k window ~204 chars, on a
        # 128k window ~3200 chars. Floor of 200 keeps stubs readable on tiny
        # windows; the divisor keeps the excerpt proportionate on huge ones.
        self.excerpt_chars = max(200, model_window // 40)
        self.exempt_tools: Set[str] = {t.lower() for t in context_cfg.distill_exempt_tools}
        self.session_root = session_root or os.path.join(tempfile.gettempdir(), DISTILLED_DIR)

    def _session_dir(self, session_id: str) -> str:
        session_dir = os.path.join(self.session_root, sanitize_fs_component(session_id))
        os.makedirs(session_dir, exist_ok=True)
        return session_dir

    def _offload(self, session_id: str, tool_name: str, serialized: str) -> str:
        """Save the full serialized result to disk and return its path."""
        session_dir = self._session_dir(session_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_name = sanitize_fs_component(tool_name) or "tool"
        filename = f"{safe_name}_{timestamp}.json"
        filepath = os.path.join(session_dir, filename)
        resolved = os.path.realpath(filepath)
        intended_dir = os.path.realpath(session_dir)
        if not (resolved == intended_dir or resolved.startswith(intended_dir + os.sep)):
            raise ValueError(f"Path traversal detected: {filepath}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(serialized)
        return filepath

    def distill(
        self,
        tool_name: str,
        result: Dict[str, Any],
        reused: bool = False,
        session_id: str = "default",
    ) -> str:
        """
        Serialize and, if oversized, offload a tool result for the model.

        Args:
            tool_name: Name of the tool.
            result: Tool execution result dict.
            reused: Whether this result was from cache.
            session_id: Session used to namespace offloaded files.

        Returns:
            JSON string ready for the tool message content field, or a
            [OUTPUT_DISTILLED] stub pointing at the saved full output.
        """
        payload: Dict[str, Any] = {
            "tool": tool_name,
            "success": bool(result.get("success", False)),
            "reused_cached_result": reused,
        }

        if result.get("error"):
            payload["error"] = str(result.get("error"))

        if "content" in result:
            payload["content"] = result.get("content")

        serialized = json.dumps(payload, ensure_ascii=False)
        tokens = self._estimator.count_tokens(serialized)

        if tokens <= self.soft_threshold:
            return serialized

        if tool_name.lower() in self.exempt_tools:
            return serialized

        filepath = self._offload(session_id, tool_name, serialized)
        content_str = str(result.get("content", ""))

        excerpt = content_str[: self.excerpt_chars]
        if len(content_str) > self.excerpt_chars:
            excerpt += "..."

        return (
            f"{DISTILLED_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Output saved to: {filepath}\n"
            f"Size: {len(content_str)} chars (~{tokens} tokens)\n"
            f"Retrieve the full output with: read_file(\"{filepath}\")\n"
            f"Excerpt: {excerpt}"
        )
