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
    from logicore.agents import BasicAgent
    
    # Define custom tools as functions
    def calculator(expression: str) -> str:
        '''Calculate a math expression.'''
        return str(eval(expression))
    
    def get_weather(city: str) -> str:
        '''Get weather for a city.'''
        return f"Weather in {city}: 25°C, Sunny"
    
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
import inspect
from typing import List, Dict, Any, Optional, Union, Callable
from datetime import datetime
from pydantic import BaseModel, Field, create_model
from logicore.providers.base import LLMProvider
from logicore.agents.agent import Agent
from logicore.tools.base import BaseTool, ToolResult


class BasicAgent:
    """
    A generic, customizable agent wrapper for the Agentry framework.
    
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
        memory_enabled: bool = True,
        debug: bool = False,
        telemetry: bool = False,
        max_iterations: int = 20,
        skills: list = None,
        workspace_root: str = None,
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
            memory_enabled: Whether to use memory middleware
            debug: Enable debug logging
            max_iterations: Maximum tool call iterations per chat
        """
        self.name = name
        self.description = description
        self.provider_name = provider
        self.model_name = model
        self.debug = debug
        self.custom_tools = tools or []
        self.custom_system_prompt = system_prompt
        
        # Build system prompt
        if system_prompt:
            self._system_prompt = system_prompt
        else:
            self._system_prompt = self._build_system_prompt()
        
        # Create the underlying agent
        self._agent = Agent(
            llm=provider,
            model=model,
            api_key=api_key,
            system_message=self._system_prompt,
            debug=debug,
            telemetry=telemetry,
            memory=memory_enabled,
            max_iterations=max_iterations,
            skills=skills,
            workspace_root=workspace_root
        )
        
        # Register custom tools
        self._register_tools()
        
        # Enable tool support if we have tools
        if self.custom_tools:
            self._agent.supports_tools = True
    
    def _build_system_prompt(self) -> str:
        """Build a Claude-style system prompt based on name and description."""
        tool_descriptions = self._get_tool_descriptions()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return f"""You are {self.name}, a custom AI assistant built with the Agentry Framework.

<identity>
{self.description}

You are designed to be helpful, efficient, and focused on your specific purpose by leveraging the tools you've been given.
</identity>
You are logicore bot designed for helping everyone on their specific use cases .
<tools>
{tool_descriptions}
</tools>

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
        
        lines = ["You have access to these tools:"]
        lines.append("")
        
        for tool in self.custom_tools:
            if isinstance(tool, BaseTool):
                lines.append(f"### `{tool.name}`")
                lines.append(f"{tool.description}")
                # Include parameter schema from BaseTool
                params = tool.schema.get("function", {}).get("parameters", {})
                properties = params.get("properties", {})
                required = params.get("required", [])
                if properties:
                    lines.append("**Parameters:**")
                    for pname, pinfo in properties.items():
                        ptype = pinfo.get("type", "string")
                        pdesc = pinfo.get("description", "")
                        req = " *(required)*" if pname in required else " *(optional)*"
                        if pdesc:
                            lines.append(f"- `{pname}` ({ptype}){req}: {pdesc}")
                        else:
                            lines.append(f"- `{pname}` ({ptype}){req}")
            elif callable(tool):
                name = tool.__name__
                doc = tool.__doc__ or "No description provided"
                lines.append(f"### `{name}`")
                lines.append(f"{doc.strip()}")
                # Include parameter info from function signature
                sig = inspect.signature(tool)
                params_list = [(p, v) for p, v in sig.parameters.items() if p != 'self']
                if params_list:
                    lines.append("**Parameters:**")
                    for pname, param in params_list:
                        ptype = param.annotation.__name__ if param.annotation != inspect.Parameter.empty else "string"
                        req = " *(required)*" if param.default == inspect.Parameter.empty else " *(optional)*"
                        lines.append(f"- `{pname}` ({ptype}){req}")
            lines.append("")
        
        lines.append("Use ONLY the exact parameter names listed above when calling tools.")
        return "\n".join(lines)
    
    def _register_tools(self):
        """Register all custom tools with the agent."""
        for tool in self.custom_tools:
            if isinstance(tool, BaseTool):
                # Already a BaseTool - register directly
                self._agent.internal_tools.append(tool.schema)
                self._agent.custom_tool_executors[tool.name] = tool.run
            elif callable(tool):
                # Convert function to tool
                self.register_tool_from_function(tool)
    
    def register_tool_from_function(self, func: Callable):
        """Convert a Python function to a tool and register it with docstring-parsed param descriptions."""
        import re
        
        name = func.__name__
        raw_doc = func.__doc__ or f"Execute {name}"
        
        # Parse docstring for param descriptions (Google + Sphinx style)
        doc_lines = raw_doc.strip().split('\n')
        description_lines = []
        param_docs = {}
        in_args = False
        
        for line in doc_lines:
            stripped = line.strip()
            sphinx = re.match(r':param\s+(\w+)\s*:(.*)', stripped)
            if sphinx:
                param_docs[sphinx.group(1)] = sphinx.group(2).strip()
                continue
            if stripped.lower() in ('args:', 'arguments:', 'parameters:', 'params:'):
                in_args = True
                continue
            if stripped.lower().rstrip(':') in ('returns', 'raises', 'yields', 'examples', 'note', 'notes'):
                in_args = False
                continue
            if in_args and stripped:
                arg_match = re.match(r'(\w+)\s*(?:\([^)]*\))?\s*:(.*)', stripped)
                if arg_match:
                    param_docs[arg_match.group(1)] = arg_match.group(2).strip()
                continue
            if not in_args and stripped:
                description_lines.append(stripped)
        
        description = ' '.join(description_lines) if description_lines else raw_doc.strip()
        
        # Get function signature
        sig = inspect.signature(func)
        params = sig.parameters
        
        # Build parameters schema
        properties = {}
        required = []
        
        for param_name, param in params.items():
            if param.annotation != inspect.Parameter.empty:
                param_type = param.annotation
            else:
                param_type = str
            
            type_mapping = {
                str: "string",
                int: "integer",
                float: "number",
                bool: "boolean",
                list: "array",
                dict: "object",
            }
            json_type = type_mapping.get(param_type, "string")
            
            pdesc = param_docs.get(param_name, f"The {param_name.replace('_', ' ')} value")
            
            properties[param_name] = {
                "type": json_type,
                "description": pdesc
            }
            
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
        
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description.strip(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
        
        def executor(**kwargs):
            try:
                result = func(**kwargs)
                return ToolResult(success=True, content=str(result))
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        
        self._agent.internal_tools.append(schema)
        self._agent.custom_tool_executors[name] = executor
        
        if self.debug:
            print(f"[BasicAgent] Registered tool: {name}")
    
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
            self._agent.custom_tool_executors[tool.name] = tool.run
        else:
            self._register_function_as_tool(tool)
    
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
            return str(eval(expression))
    """
    def decorator(func: Callable) -> Callable:
        if description:
            func.__doc__ = description
        func._is_tool = True
        return func
    return decorator
