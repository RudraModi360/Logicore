"""
AgentPromptMixin: System prompt management extracted from Agent.

Consolidates system prompt rebuilding and tool verification logic.

Agent inherits from this mixin to maintain the same public API.
"""

from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AgentPromptMixin:
    """Mixin providing system prompt management for Agent.

    Expects the following attributes to be set on the host class:
    - internal_tools: List[Dict]
    - context_engine: ContextEngine
    - _custom_system_message: Optional[str]
    - default_system_message: str
    - model_name: str
    - role: str
    - _plan_mode_enabled: bool
    - sessions: Dict[str, Any]
    - tool_executor: ToolExecutor
    - _build_skills_prompt_section: Callable
    """

    def _rebuild_system_prompt_with_tools(self):
        from logicore.config.prompts import _format_tools, get_system_prompt

        tool_prompt_cap = 60
        visible_tools = self.internal_tools[:tool_prompt_cap]
        hidden_count = max(0, len(self.internal_tools) - len(visible_tools))
        tools_section = _format_tools(visible_tools)
        skills_section = self._build_skills_prompt_section()

        assembler = self.context_engine.prompt_assembler

        if self._custom_system_message:
            self.default_system_message = assembler.patch_custom_prompt(
                self._custom_system_message, tools_section, skills_section
            )
        else:
            base_prompt = get_system_prompt(
                model_name=self.model_name,
                role=self.role,
                tools=self.internal_tools,
                plan_mode=self._plan_mode_enabled,
            )
            self.default_system_message = assembler.assemble(
                base_prompt, tools_section, skills_section, hidden_count
            )

        for session in self.sessions.values():
            if session.messages and session.messages[0].get("role") == "system":
                session.messages[0]["content"] = self.default_system_message

        self._verify_prompt_tools(self.internal_tools, self.default_system_message)

    def _verify_prompt_tools(self, tool_schemas: list, prompt_text: str) -> list:
        """Verify prompt-documented tools are actually executable.

        Returns sorted list of phantom tool names found (empty if none).
        """
        executable = set(self.tool_executor.get_registered_tool_names())

        schema_phantoms: set = set()
        for t in (tool_schemas or []):
            name = t.get("function", {}).get("name") if isinstance(t, dict) else None
            if name and name not in executable:
                schema_phantoms.add(name)

        call_refs = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", prompt_text or ""))
        ref_phantoms = {n for n in call_refs if n not in executable}

        if schema_phantoms:
            logger.warning(
                "[Agent] System prompt lists tools with no registered executor "
                "(phantom tools): %s", sorted(schema_phantoms)
            )
        if ref_phantoms:
            logger.debug(
                "[Agent] Prompt references tool-like names with no executor "
                "(review for drift): %s", sorted(ref_phantoms)
            )

        return sorted(schema_phantoms | ref_phantoms)
