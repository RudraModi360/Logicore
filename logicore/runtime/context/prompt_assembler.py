"""
PromptAssembler: System prompt construction and token-based truncation.

Extracted from Agent._rebuild_system_prompt_with_tools().
Handles system prompt building and truncation at the token limit,
not the character limit, so the framework measures the same unit the
LLM counts. This removes the char-vs-token mismatch that previously
caused premature truncation (e.g. a 40k-char prompt reported as 40k
"tokens" when it was really ~10-11k tokens).
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

from logicore.runtime.context.token_estimator import TokenEstimator


# Default token budget for the system prompt (overridable via config).
SYSTEM_PROMPT_MAX_TOKENS = 16000


class PromptAssembler:
    """
    Builds and truncates system prompts.

    Responsibilities:
    - Assemble system prompt from base prompt + tools + skills
    - Truncate at max_tokens to stay within context budget (token-accurate)
    - Update system message in existing sessions
    """

    def __init__(
        self,
        max_tokens: int = SYSTEM_PROMPT_MAX_TOKENS,
        token_estimator: Optional[TokenEstimator] = None,
        debug: bool = False,
    ):
        self.max_tokens = max_tokens
        self.token_estimator = token_estimator or TokenEstimator()
        self.debug = debug

    def assemble(
        self,
        base_prompt: str,
        tools_section: str,
        skills_section: str = "",
        hidden_tool_count: int = 0,
    ) -> str:
        """
        Assemble the full system prompt from components.

        Args:
            base_prompt: The base system prompt (auto-generated or custom)
            tools_section: Formatted tool schemas
            skills_section: Skill instructions
            hidden_tool_count: Number of tools omitted for context efficiency

        Returns:
            Assembled and token-truncated system prompt
        """
        prompt = base_prompt

        # Append tools section if not already embedded in base_prompt
        if tools_section and tools_section not in prompt:
            prompt += tools_section

        if hidden_tool_count > 0:
            prompt += (
                f"\n\n- Note: {hidden_tool_count} additional tools are available at runtime "
                "but omitted from prompt docs for context efficiency."
            )

        if skills_section:
            prompt += skills_section

        # Truncate if too long — use token-accurate measurement + semantic boundary
        if self.token_estimator.count_tokens(prompt) > self.max_tokens:
            prompt = self._truncate_semantic(prompt, self.max_tokens)
            if self.debug:
                print(
                    f"[ContextEngine] System prompt exceeded {self.max_tokens} tokens "
                    f"and was truncated"
                )

        return prompt

    def _truncate_semantic(self, prompt: str, max_tokens: int) -> str:
        """
        Truncate prompt to a token budget at a semantic boundary
        (section header or newline) rather than mid-word or mid-tag.

        Works by walking the prompt and cutting at the largest character
        offset whose token count stays within budget, then snapping back to
        the nearest clean boundary.
        """
        total_tokens = self.token_estimator.count_tokens(prompt)
        if total_tokens <= max_tokens:
            return prompt

        # Binary search for the char length that lands at/under the budget.
        lo, hi = 0, len(prompt)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.token_estimator.count_tokens(prompt[:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1

        truncated = prompt[:lo]

        # Snap back to a clean semantic boundary (## header, else newline).
        last_header = truncated.rfind("\n## ")
        if last_header > 0:
            truncated = truncated[:last_header]
        else:
            last_newline = truncated.rfind("\n")
            if last_newline > 0:
                truncated = truncated[:last_newline]

        return truncated + "\n\n[System prompt truncated for context efficiency.]"

    def patch_custom_prompt(
        self,
        custom_prompt: str,
        tools_section: str,
        skills_section: str = "",
    ) -> str:
        """
        Patch a custom system prompt by replacing tool sections.

        Supports two formats:
        - <available_tools>...</available_tools> XML tags
        - ## Available Tools markdown headers
        """
        if "<available_tools>" in custom_prompt:
            prompt = re.sub(
                r"<available_tools>[\s\S]*?</available_tools>",
                tools_section.strip() if tools_section else "",
                custom_prompt,
            )
        elif "## Available Tools" in custom_prompt:
            prompt = re.sub(
                r"## Available Tools[\s\S]*?(?=\n## |\Z)",
                tools_section.strip() + "\n",
                custom_prompt,
            )
        else:
            prompt = custom_prompt + tools_section

        if skills_section:
            prompt += skills_section

        # Apply the same token-based cap to custom prompts.
        if self.token_estimator.count_tokens(prompt) > self.max_tokens:
            prompt = self._truncate_semantic(prompt, self.max_tokens)
            if self.debug:
                print(
                    f"[ContextEngine] Custom system prompt exceeded {self.max_tokens} "
                    f"tokens and was truncated"
                )

        return prompt

    @staticmethod
    def propagate_to_sessions(
        sessions: Dict[str, Any], new_system_message: str
    ) -> int:
        """
        Update system message in all existing sessions.

        Returns the number of sessions updated.
        """
        updated = 0
        for session in sessions.values():
            if session.messages and session.messages[0].get("role") == "system":
                session.messages[0]["content"] = new_system_message
                updated += 1
        return updated
