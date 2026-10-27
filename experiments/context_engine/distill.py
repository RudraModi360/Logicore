"""
ToolOutputDistiller (Group B): distill bulky tool outputs AT INSERTION time.

Masking (Group A) is REACTIVE — it waits for a tool output to already sit in the
session, then masks it once the budget is pressured. Distillation is PREVENTIVE —
it runs the moment a tool returns, BEFORE the output is JSON-wrapped and appended
to the session, so the bulky payload never enters the context window at all.

Hook point: the demo wraps each TOOL_IMPLS entry with distiller.distill(). run_turn()
calls impl(args) -> gets the distilled string -> tracer + session both see the
distilled version (one truth), and the full output is saved to disk (retrievable
via read_file, same story as masking).

Nothing here is imported by the framework. Wire it into the sandbox, watch the
traces, and only once it looks right do we port the delta into logicore/.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from logicore.runtime.context.masking import MASKING_INDICATOR  # noqa: F401 (parity reference)

DISTILLED_INDICATOR = "[OUTPUT_DISTILLED]"

# Retrieval + interactive tools that must never be distilled. read_file is the
# retrieval path our own stubs instruct the agent to use — distilling its result
# would make the stub self-defeating (agent asks for the full output, gets another
# stub). ask_user-style tools are interactive and must stay verbatim.
DISTILL_EXEMPT_TOOLS: set = {
    "read_file",
    "ask_user",
    "human_feedback",
    "get_user_input",
    "confirm",
}


class ToolOutputDistiller:
    """Distill bulky tool outputs to an excerpt + on-disk path at insertion.

    Args:
        estimator: framework TokenEstimator (single token truth; same one the
            budget/masking use, so the threshold decision is consistent).
        threshold_tokens: distill results whose token count exceeds this.
        excerpt_chars: how many leading chars of the original to keep inline.
        temp_dir: where full outputs are saved (defaults to system temp).
    """

    def __init__(
        self,
        estimator: Any,
        threshold_tokens: int = 1500,
        excerpt_chars: int = 200,
        temp_dir: Optional[str] = None,
    ):
        self._estimator = estimator
        self.threshold_tokens = int(threshold_tokens)
        self.excerpt_chars = int(excerpt_chars)
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self.stats: Dict[str, int] = {"checked": 0, "distilled": 0, "tokens_saved": 0}

    def count_tokens(self, text: str) -> int:
        return self._estimator.count_tokens(text or "")

    def should_distill(self, result: str, tool_name: str = "") -> bool:
        """Distill only when the payload is large enough to matter."""
        if not result:
            return False
        if tool_name.lower() in DISTILL_EXEMPT_TOOLS:
            return False
        if result.startswith("ERROR"):
            return False
        if DISTILLED_INDICATOR in result or MASKING_INDICATOR in result:
            return False
        return self.count_tokens(result) > self.threshold_tokens

    def distill(self, tool_name: str, result: str, session_id: str = "default") -> str:
        """Return a distilled stub for bulky results; pass small results through."""
        self.stats["checked"] += 1
        if not self.should_distill(result, tool_name):
            return result

        original_tokens = self.count_tokens(result)
        filepath = self._save(result, tool_name, session_id)
        stub = self._build_stub(result, tool_name, filepath, original_tokens)

        saved = original_tokens - self.count_tokens(stub)
        self.stats["distilled"] += 1
        self.stats["tokens_saved"] += max(0, saved)
        return stub

    def _save(self, content: str, tool_name: str, session_id: str) -> str:
        """Save the full output to disk and return its path (path-traversal safe)."""
        sanitized_tool = re.sub(r"[^\w\-]", "_", tool_name)
        sanitized_tool = re.sub(r"_+", "_", sanitized_tool).strip("_")[:50] or "tool"
        sanitized_session = re.sub(r"[^\w\-]", "_", session_id)
        sanitized_session = re.sub(r"_+", "_", sanitized_session).strip("_")[:50] or "session"

        session_dir = os.path.join(self.temp_dir, "logicore_distilled", sanitized_session)
        os.makedirs(session_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filepath = os.path.join(session_dir, f"{sanitized_tool}_{timestamp}.txt")

        resolved = os.path.realpath(filepath)
        intended_dir = os.path.realpath(session_dir)
        if not resolved.startswith(intended_dir):
            raise ValueError(f"Path traversal detected: {filepath}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def _build_stub(
        self, original: str, tool_name: str, filepath: str, original_tokens: int,
    ) -> str:
        line_count = original.count("\n") + 1
        char_count = len(original)
        excerpt = original[: self.excerpt_chars]
        if len(original) > self.excerpt_chars:
            excerpt += "..."
        return (
            f"{DISTILLED_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Full output: {char_count} chars (~{original_tokens} tokens), "
            f"{line_count} lines — saved to: {filepath}\n"
            f"Excerpt (first {self.excerpt_chars} chars):\n"
            f"---\n{excerpt}\n---\n"
            f"Instruction: output was distilled to preserve the context window. "
            f"For the full output use read_file('{filepath}')."
        )


def wrap_tool_impls(
    tool_impls: Dict[str, Callable[[Dict[str, Any]], str]],
    distiller: ToolOutputDistiller,
    session_id: str = "default",
) -> int:
    """Wrap every tool impl so its result is distilled before entering the session.

    Returns the number of tools wrapped. Wrapping is idempotent-safe: it captures
    the CURRENT impl, so re-wrapping would double-distill (harmless but wasteful);
    call once at startup.
    """
    count = 0
    for name in list(tool_impls.keys()):
        original = tool_impls[name]

        def make_wrapper(tool_name: str, impl: Callable[[Dict[str, Any]], str]):
            def wrapper(args: Dict[str, Any]) -> str:
                result = impl(args)
                return distiller.distill(tool_name, result, session_id)
            return wrapper

        tool_impls[name] = make_wrapper(name, original)
        count += 1
    return count
