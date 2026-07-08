from typing import List, Dict, Any, Union
from logicore.agent.base import Agent
from logicore.agent.session import AgentSession
from logicore.providers.base import LLMProvider
from logicore.config.prompts import get_system_prompt, get_copilot_prompt


class CopilotAgent(Agent):
    """
    A specialized Agent optimized for coding tasks (Copilot-like experience).
    
    Features:
    - Pre-loaded with filesystem and execution tools
    - Coding-focused Claude-style system prompt
    - Convenience methods for code explanation and review
    
    Tool Loading Strategy:
    - Uses 'copilot' preset by default (~15 tools)
    - Includes: filesystem, execution, process management, git, web tools
    - Optimized for coding workflows
    """
    
    def __init__(
        self, 
        provider: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        system_prompt: str = None,
        debug: bool = False,
        tools: list|bool = True,
        capabilities: Any = None,
        telemetry: bool = False,
        skills: list = None,
        workspace_root: str = None,
        tool_preset: str = "copilot",
    ):
        # Use copilot-specific prompt if no custom message provided
        if not system_prompt:
            model_name = model or "Unknown Model"
            system_prompt = get_copilot_prompt(model_name)
        
        super().__init__(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            role="copilot",
            debug=debug,
            tools=tools,
            telemetry=telemetry,
            skills=skills,
            workspace_root=workspace_root,
            tool_preset=tool_preset,
        )
        
    async def chat(self, user_input: Union[str, List[Dict[str, Any]]], session_id: str = "default", stream: bool = False, generate_walkthrough: bool = False, **kwargs) -> str:
        """Coding-optimized chat."""
        return await super().chat(user_input, session_id=session_id, stream=stream, generate_walkthrough=generate_walkthrough, **kwargs)

    async def explain_code(self, code: str, language: str = None, stream: bool = False) -> str:
        """
        Convenience method to explain a piece of code.
        
        Args:
            code: The code to explain
            language: Optional language hint (e.g., 'python', 'javascript')
        """
        lang_tag = language or ""
        prompt = f"Please explain the following code concisely:\n\n```{lang_tag}\n{code}\n```"
        return await self.chat(prompt, stream=stream)

    async def review_file(self, filepath: str, stream: bool = False) -> str:
        """
        Convenience method to review a file for bugs and improvements.
        
        Args:
            filepath: Path to the file to review
        """
        prompt = f"Please review the file '{filepath}' for potential bugs, improvements, and security issues. Read the file first."
        return await self.chat(prompt, stream=stream)
    
    async def write_code(self, description: str, language: str = "python", stream: bool = False) -> str:
        """
        Convenience method to generate code based on a description.
        
        Args:
            description: What the code should do
            language: Target programming language
        """
        prompt = f"Write {language} code that: {description}"
        return await self.chat(prompt, stream=stream)
    
    async def fix_bug(self, code: str, error: str = None, stream: bool = False) -> str:
        """
        Convenience method to fix a bug in code.
        
        Args:
            code: The buggy code
            error: Optional error message or description of the issue
        """
        if error:
            prompt = f"Fix this bug. Error: {error}\n\nCode:\n```\n{code}\n```"
        else:
            prompt = f"Find and fix any bugs in this code:\n\n```\n{code}\n```"
        return await self.chat(prompt, stream=stream)

    async def discuss(self, user_input: str, stream: bool = False) -> str:
        """
        Conducts a general chat session (acting as a normal assistant).
        Uses a separate session ID 'general' to keep context separate from coding tasks.
        """
        session_id = "general"
        if session_id not in self.sessions:
            # Create session with General Agent prompt
            model_name = getattr(self.provider, "model_name", "Unknown")
            prompt = get_system_prompt(model_name, role="general")
            self.sessions[session_id] = AgentSession(session_id, prompt)
            
        return await self.chat(user_input, session_id=session_id, stream=stream)
