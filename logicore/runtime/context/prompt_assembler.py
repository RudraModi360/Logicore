"""
PromptAssembler: System prompt construction and truncation.

Extracted from Agent._rebuild_system_prompt_with_tools().
Handles system prompt building, truncation at the character limit,
and propagation to existing sessions.
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional


# Hard limit for system prompt characters
SYSTEM_PROMPT_MAX_CHARS = 40000


class PromptAssembler:
    """
    Builds and truncates system prompts.

    Responsibilities:
    - Assemble system prompt from base prompt + tools + skills
    - Truncate at max_prompt_chars to stay within context budget
    - Update system message in existing sessions
    """

    def __init__(self, max_chars: int = SYSTEM_PROMPT_MAX_CHARS, debug: bool = False):
        self.max_chars = max_chars
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
            Assembled and truncated system prompt
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

        # Truncate if too long — use semantic-aware boundary detection
        if len(prompt) > self.max_chars:
            prompt = self._truncate_semantic(prompt, self.max_chars)
            if self.debug:
                print(
                    f"[ContextEngine] System prompt exceeded {self.max_chars} chars and was truncated"
                )

        return prompt

    @staticmethod
    def _truncate_semantic(prompt: str, max_chars: int) -> str:
        """
        Truncate prompt at a semantic boundary (section header or newline)
        rather than mid-word or mid-tag. Falls back to max_chars if no good
        boundary is found within the last 500 characters.
        """
        truncated = prompt[:max_chars]
        # Search backwards for a clean section boundary (## header)
        last_header = truncated.rfind("\n## ")
        if last_header > max_chars - 500:
            truncated = truncated[:last_header]
        else:
            # Fall back to last newline
            last_newline = truncated.rfind("\n")
            if last_newline > max_chars - 500:
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