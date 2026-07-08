"""
BasicAgent - A Generic, Customizable Agent Wrapper

This is the simplest way to create an AI agent with logicore.
Users can configure their own:
- LLM provider and model
- Custom tools
- Agent name and description
- System prompt

Similar to popular frameworks like LangChain, CrewAI, etc.

Example Usage:
    from logicore import BasicAgent
    import ast

    # Define custom tools as functions
    def calculator(expression: str) -> str:
        '''Calculate a math expression safely.'''
        import operator
        ops = {
            operator.add: '+', operator.sub: '-', operator.mul: '*',
            operator.truediv: '/', operator.floordiv: '//', operator.mod: '%',
            operator.pow: '**'
        }
        # Safe evaluation using ast.literal_eval for numbers only
        try:
            tree = ast.parse(expression, mode='eval')
            return str(eval(compile(tree, '<calc>', 'eval'), {"__builtins__": {}}, {}))
        except Exception:
            return f"Error: Cannot evaluate '{expression}' safely"

    def get_weather(city: str) -> str:
        '''Get weather for a city.'''
        return f"Weather in {city}: 25C, Sunny"
    
    # Create agent
    agent = BasicAgent(
        name="MyAssistant",
        description="A helpful assistant that can calculate and check weather",
        provider="ollama",
        model="llama3.2:3b",
        tools=[calculator, get_weather]
    )
    
    # Use it
    response = await agent.chat("What is 15 * 4?")
"""

import asyncio
from typing import List, Dict, Any, Union, Callable
from datetime import datetime
from logicore.agent.base import Agent
from logicore.tools.base import BaseTool, ToolResult


class BasicAgent:
    """
    A generic, customizable agent wrapper for the Logicore framework.
    
    This is the simplest way to create an AI agent. Just provide:
    - name: Your agent's name
    - description: What your agent does
    - provider: LLM provider (ollama, groq, gemini)
    - model: Model name
    - tools: List of functions or BaseTool instances
    
    The agent will automatically:
    - Convert functions to tools with proper schemas
    - Handle tool execution
    - Manage conversation context
    - Stream responses (if callback provided)
    
    Example:
        agent = BasicAgent(
            name="Calculator",
            description="A math helper",
            tools=[my_calc_function],
            provider="ollama",
            model="llama3.2:3b"
        )
        
        response = await agent.chat("What is 10 + 5?")
    """
    
    def __init__(
        self,
        name: str = "Assistant",
        description: str = "A helpful AI assistant",
        provider: str = "ollama",
        model: str = None,
        api_key: str = None,
        tools: List[Union[Callable, BaseTool]] = None,
        system_prompt: str = None,
        debug: bool = False,
        telemetry: bool = False,
        max_iterations: int = 20,
        skills: list = None,
        workspace_root: str = None,
        tool_preset: str = None,
        **kwargs
    ):
        """
        Create a new BasicAgent.
        
        Args:
            name: Name of the agent (e.g., "ResearchBot", "CodeHelper")
            description: What this agent does (used in system prompt)
            provider: LLM provider - "ollama", "groq", or "gemini"
            model: Model name (provider-specific)
            api_key: API key for cloud providers (groq, gemini)
            tools: List of tools - can be functions or BaseTool instances
            system_prompt: Custom system prompt (optional, auto-generated if not provided)
            debug: Enable debug logging
            max_iterations: Maximum tool call iterations per chat
            tool_preset: Tool preset to use ("lightweight", "minimal", "webdev", "smart", "copilot", "full")
                         If provided, loads preset tools IN ADDITION to custom tools.
        """
        self.name = name
        self.description = description
        self.provider_name = provider
        self.model_name = model
        self.debug = debug
        self.custom_tools = tools or []
        self.custom_system_prompt = system_prompt
        
        # Create the underlying agent first
        self._agent = Agent(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt or "",
            debug=debug,
            telemetry=telemetry,
            max_iterations=max_iterations,
            skills=skills,
            workspace_root=workspace_root,
            tool_preset=tool_preset,
        )
        
        # Register custom tools first (needed for tool descriptions in system prompt)
        self._register_tools()
        
        # Build system prompt (now that tools are registered)
        if system_prompt:
            self._system_prompt = system_prompt
        else:
            self._system_prompt = self._build_system_prompt()
        
        # Update agent with final system prompt
        self._agent.default_system_message = self._system_prompt
        
        # Enable tool support if we have tools
        if self.custom_tools or self._agent.supports_tools:
            self._agent.supports_tools = True
    
    def _build_system_prompt(self) -> str:
        """Build a Claude-style system prompt based on name and description."""
        from logicore.config.prompts import _get_task_tracking_section
        tool_descriptions = self._get_tool_descriptions()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_section = _get_task_tracking_section()
        
        return f"""You are {self.name}, a custom AI assistant built with the Logicore Framework.

<identity>
{self.description}

You are designed to be helpful, efficient, and focused on your specific purpose by leveraging the tools you've been given.
</identity>
You are logicore bot designed for helping everyone on their specific use cases .
<tools>
{tool_descriptions}
</tools>

{task_section}

<guidelines>
1. **Use Your Tools**: When a task matches one of your tools, use it. Don't try to do things manually that tools can do.

2. **Be Direct**: Lead with the answer or action, not preamble. Get to the point.

3. **Be Accurate**: If you're unsure, say so. Don't guess or make up information.

4. **Explain When Helpful**: Briefly explain what you're doing when using tools, but don't over-explain obvious things.

5. **Ask for Clarity**: If a request is ambiguous, ask a focused clarifying question.
</guidelines>

<current_context>
- Current time: {current_time}
- Session: Active
</current_context>

You are ready to help. Use your tools effectively.
"""
    
    def _get_tool_descriptions(self) -> str:
        """Get formatted descriptions of all tools with full parameter schema."""
        if not self.custom_tools:
            return "You have no tools available. Respond using your knowledge only."
        
        from logicore.config.prompts import _format_tools
        
        # Use schemas from already-registered tools (internal_tools)
        tool_schemas = [t for t in self._agent.internal_tools]
        
        # Also include any BaseTool instances not yet registered
        for tool in self.custom_tools:
            if isinstance(tool, BaseTool):
                # Check if not already registered
                registered_names = {t.get("function", {}).get("name") for t in tool_schemas}
                if tool.name not in registered_names:
                    tool_schemas.append(tool.schema)
        
        return _format_tools(tool_schemas)
    
    def _register_tools(self):
        """Register all custom tools with the agent."""
        for tool in self.custom_tools:
            if isinstance(tool, BaseTool):
                # Already a BaseTool - register via public API
                self._agent.internal_tools.append(tool.schema)
                self._agent.tool_executor.register_custom_tool(tool.name, tool.run)
            elif callable(tool):
                # Use base Agent's register_tool_from_function (no duplication)
                self._agent.register_tool_from_function(tool)
    
    # --- Public API ---
    
    async def chat(self, message: Union[str, List[Dict[str, Any]]], session_id: str = "default", stream: bool = False, generate_walkthrough: bool = False, **kwargs) -> str:
        """
        Send a message and get a response.
        
        Args:
            message: The user's message (str or list with multimodal content)
            session_id: Session ID for conversation context
            stream: Whether to stream the response
            generate_walkthrough: Provide a summary of execution at the end
            
        Returns:
            The agent's response
        """
        return await self._agent.chat(message, session_id=session_id, stream=stream, generate_walkthrough=generate_walkthrough, **kwargs)
    
    def chat_sync(self, message: str, session_id: str = "default", generate_walkthrough: bool = False) -> str:
        """
        Synchronous version of chat.
        
        Args:
            message: The user's message
            session_id: Session ID for conversation context
            generate_walkthrough: Provide a summary of execution at the end
            
        Returns:
            The agent's response
        """
        return asyncio.run(self.chat(message, session_id=session_id, generate_walkthrough=generate_walkthrough))
    
    def set_callbacks(
        self,
        on_token: Callable[[str], None] = None,
        on_tool_start: Callable[[str, str, dict], None] = None,
        on_tool_end: Callable[[str, str, Any], None] = None,
        on_final_message: Callable[[str, str], None] = None
    ):
        """
        Set callbacks for streaming and tool events.
        
        Args:
            on_token: Called for each streaming token
            on_tool_start: Called when a tool starts (session_id, name, args)
            on_tool_end: Called when a tool ends (session_id, name, result)
            on_final_message: Called when response is complete (session_id, content)
        """
        self._agent.set_callbacks(
            on_token=on_token,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_final_message=on_final_message
        )
    
    def add_tool(self, tool: Union[Callable, BaseTool]):
        """
        Add a new tool to the agent.

        Args:
            tool: A function or BaseTool instance
        """
        self.custom_tools.append(tool)
        if isinstance(tool, BaseTool):
            self._agent.internal_tools.append(tool.schema)
            self._agent.tool_executor.register_custom_tool(tool.name, tool.run)
        else:
            # Use base Agent's register_tool_from_function (no duplication)
            self._agent.register_tool_from_function(tool)
    
    def add_tools(self, tools: List[Union[Callable, BaseTool]]):
        """Add multiple tools at once."""
        for tool in tools:
            self.add_tool(tool)
    
    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """
        Get all available tools (internal + MCP).
        
        Returns:
            List of tool schemas with function definition and parameters
        """
        return await self._agent.get_all_tools()
    
    def clear_history(self, session_id: str = "default"):
        """Clear conversation history for a session."""
        self._agent.clear_session(session_id)
    
    def get_session(self, session_id: str = "default"):
        """Get conversation session."""
        return self._agent.get_session(session_id)
    
    @property
    def tools(self) -> List[str]:
        """Get list of registered tool names."""
        return [t.get("function", {}).get("name") for t in self._agent.internal_tools]
    
    @property
    def system_prompt(self) -> str:
        """Get the current system prompt."""
        return self._system_prompt
    
    @property
    def telemetry(self) -> Dict[str, Any]:
        """
        Get telemetry summary for the agent's sessions.
        
        Returns:
            Dictionary with token usage, performance metrics, and context info.
        """
        return self._agent.telemetry

    async def cleanup(self):
        """Cleanup resources."""
        await self._agent.cleanup()
    
    def load_skill(self, skill):
        """Load a single skill into the agent."""
        self._agent.load_skill(skill)

    def load_skills(self, skills: list):
        """Load multiple skills by name or Skill objects."""
        self._agent.load_skills(skills)

    @property
    def loaded_skills(self) -> list:
        """Get list of loaded skill names."""
        return [s.name for s in self._agent.skills]

    def __repr__(self):
        return f"BasicAgent(name='{self.name}', provider='{self.provider_name}', tools={self.tools})"


# --- Convenience Functions ---

def create_agent(
    name: str = "Assistant",
    description: str = "A helpful AI assistant",
    tools: List[Callable] = None,
    provider: str = "ollama",
    model: str = None,
    api_key: str = None,
    **kwargs
) -> BasicAgent:
    """
    Quick way to create a BasicAgent.
    
    Example:
        agent = create_agent(
            name="MathBot",
            description="Helps with math",
            tools=[add, subtract, multiply],
            provider="ollama",
            model="llama3.2:3b"
        )
    """
    return BasicAgent(
        name=name,
        description=description,
        tools=tools,
        provider=provider,
        model=model,
        api_key=api_key,
        **kwargs
    )


def tool(description: str = None):
    """
    Decorator to mark a function as a tool with a description.

    Example:
        @tool("Calculate a math expression")
        def calculator(expression: str) -> str:
            import ast
            return str(ast.literal_eval(expression))
    """
    def decorator(func: Callable) -> Callable:
        if description:
            func.__doc__ = description
        func._is_tool = True
        return func
    return decorator
