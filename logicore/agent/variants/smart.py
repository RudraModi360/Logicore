from typing import List, Dict, Any, Union
from logicore.providers.base import LLMProvider
from logicore.agent.base import Agent
from logicore.config.prompts import get_smart_agent_solo_prompt
import logging

logger = logging.getLogger(__name__)


class SmartAgent(Agent):
    """
    A versatile AI Agent optimized for complex reasoning tasks.

    Key Features:
    - Enhanced reasoning with step-by-step problem solving
    - Essential tools (web, bash, notes, datetime)
    - Tool deduplication and smart execution

    Tool Loading Strategy:
    - Uses 'smart' preset by default (~30 tools)
    - Includes: bash, datetime, notes, think, filesystem, web, cron, tracker, plan tools
    - Can be customized via tool_preset parameter
    """

    def __init__(
        self,
        provider: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        system_prompt: str = None,
        tools: list = None,
        debug: bool = False,
        telemetry: bool = False,
        max_iterations: int = 40,
        skills: list = None,
        workspace_root: str = None,
        tool_preset: str = "smart",
        storage=None,
    ):
        super().__init__(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            role="general",
            debug=debug,
            telemetry=telemetry,
            max_iterations=max_iterations,
            tools=tools,
            skills=skills,
            workspace_root=workspace_root,
            tool_preset=tool_preset,
            storage=storage,
        )

        if not system_prompt:
            self._update_system_message()

    def _update_system_message(self):
        """Set the system prompt for this agent, including skills."""
        model_name = getattr(self.provider, "model_name", "Unknown")
        tools_for_prompt = self.internal_tools if hasattr(self, 'internal_tools') else []

        base_prompt = get_smart_agent_solo_prompt(
            model_name=model_name,
            tools=tools_for_prompt
        )

        self._custom_system_message = base_prompt
        self.default_system_message = base_prompt
        
        # Rebuild with skills if any are loaded
        if self.skills:
            self._rebuild_system_prompt_with_tools()

    async def reason(self, problem: str, session_id: str = None) -> str:
        """
        Explicitly request step-by-step reasoning for a problem.
        """
        prompt = f"""Please think through this problem step by step using the 'think' tool:

{problem}

After reasoning, provide your conclusion and solution."""

        return await self.chat(prompt, session_id)

    def status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            "model": getattr(self.provider, "model_name", "Unknown"),
            "tools_loaded": len(self.internal_tools),
            "sessions_active": len(self.sessions),
        }
