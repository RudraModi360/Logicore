"""
HallucinationChecker: Detects response claims that contradict tool execution results.

Extracted from ChatOrchestrator to separate hallucination detection from the
chat loop. Performs dynamic verification by comparing LLM claims against
actual execution history.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


# Compiled regex patterns for hallucination detection
_COMPLETION_CLAIMS = re.compile(
    r"\b(completed?|fixed?|resolved?|created?|updated?|deleted?|installed?|configured?|set up|deployed?|migrated?|done|success|finished|saved|written|applied)\b",
    re.IGNORECASE,
)
_TOOL_SUCCEEDED_RE = re.compile(r"^Tool (\S+) SUCCEEDED$")
_TOOL_FAILED_RE = re.compile(r"^Tool (\S+) FAILED:")


def check_response_hallucination(
    response: str,
    execution_log: List[str],
    successful_tools_this_chat: int,
) -> Optional[str]:
    """Compare response claims against actual tool execution results.

    Returns a warning string if the response claims success but tools
    actually failed, or claims work was done but no tools were called.
    This is *dynamic* — it checks real execution history, not static rules.

    Args:
        response: The LLM's response text to check.
        execution_log: List of execution log entries (e.g., "Tool X SUCCEEDED").
        successful_tools_this_chat: Number of tools that succeeded this turn.

    Returns:
        Warning string if hallucination detected, None otherwise.
    """
    if not response or not response.strip():
        return None

    claims = _COMPLETION_CLAIMS.findall(response)
    if not claims:
        return None

    # Collect tool execution outcomes from the log
    tool_outcomes: Dict[str, str] = {}
    for entry in execution_log:
        if not isinstance(entry, str):
            continue
        m_suc = _TOOL_SUCCEEDED_RE.match(entry)
        m_fail = _TOOL_FAILED_RE.match(entry)
        if m_suc:
            tool_outcomes[m_suc.group(1)] = "succeeded"
        elif m_fail:
            tool_outcomes[m_fail.group(1)] = "failed"

    failed_tools = [t for t, s in tool_outcomes.items() if s.upper() in ("ERROR", "FAILED")]
    succeeded_tools = [t for t, s in tool_outcomes.items() if s.upper() in ("COMPLETED", "SUCCEEDED")]

    # Case 1: Response claims success but ALL tools failed
    if failed_tools and not succeeded_tools and successful_tools_this_chat == 0:
        return (
            f"Your response claims work was done, but every tool call "
            f"failed ({', '.join(failed_tools[:3])}). "
            f"Either retry with a different approach or explain what went wrong."
        )

    # Case 2: Response claims specific tool success but that tool actually failed
    mentioned_tool_claims = re.findall(
        r"(?:used|called|executed|ran)\s+(\w+)", response, re.IGNORECASE
    )
    for tool_name in mentioned_tool_claims:
        if tool_name.lower() in {t.lower() for t in failed_tools}:
            return (
                f"Your response mentions successfully using '{tool_name}', "
                f"but that tool actually failed. Correct your response or retry."
            )

    # Case 3: No tools called but response claims completion
    if successful_tools_this_chat == 0 and len(claims) >= 2:
        return (
            "You are claiming multiple things were completed, but no tools "
            "were executed this turn. Either run the tools or remove the claims."
        )

    return None
