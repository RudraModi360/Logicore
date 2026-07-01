from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from logicore.providers.base import LLMProvider
from logicore.agent.base import Agent
from logicore.tools.datetime import get_smart_agent_tools, get_smart_agent_tool_schemas
from logicore.tools.datetime import DateTimeTool
from logicore.tools.notes import NotesTool
from logicore.tools.bash import SmartBashTool
from logicore.tools.think import ThinkTool
from logicore.tools.cron import AddCronJobTool, ListCronJobsTool, RemoveCronJobTool, GetCronsTool
from logicore.config.prompts import (
    get_system_prompt,
    get_smart_agent_solo_prompt,
    get_smart_agent_project_prompt
)
from logicore.memory.project import ProjectContext
import os

# Load API credentials from environment variables
# Set these in your .env file or as system environment variables
# GOOGLE_API_KEY and GOOGLE_CX should be set before importing this module

class SmartAgentMode:
    """Agent operation modes."""
    SOLO = "solo"           # General chat, greater reasoning focus
    PROJECT = "project"     # Project-centered with context awareness


class SmartAgent(Agent):
    """
    A versatile AI Agent optimized for:
    - Simple to complex reasoning tasks
    - Project-based work with context memory
    - Solo chat with enhanced reasoning
    
    Key Features:
    - Pluggable project memory
    - Essential tools (web, bash, notes, datetime, memory)
    - Mode switching (project/solo)
    - Automatic learning capture
    """
    
    def __init__(
        self,
        llm: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        mode: str = SmartAgentMode.SOLO,
        project_id: str = None,
        debug: bool = False,
        telemetry: bool = False,
        memory: bool = False,
        max_iterations: int = 40,
        capabilities: Any = None,
        skills: list = None,
        workspace_root: str = None
    ):
        # Initialize base agent
        super().__init__(
            llm=llm,
            model=model,
            api_key=api_key,
            system_message=None,  # Will be set based on mode
            role="general",
            debug=debug,
            telemetry=telemetry,
            memory=memory,
            max_iterations=max_iterations,
            # capabilities=capabilities,
            skills=skills,
            workspace_root=workspace_root
        )
        
        # Smart Agent specific
        self.mode = mode
        self.project_id = project_id
        # Memory system has been removed - using stubs for compatibility
        from logicore.memory.project import ProjectMemory, ProjectContext
        self.project_memory = ProjectMemory()
        self.project_context: Optional[ProjectContext] = None
        
        # Set appropriate system message
        self._update_system_message()
        
        # Load Smart Agent tools
        self._load_smart_tools()
    
    def _update_system_message(self):
        """Update system message based on mode and project context."""
        model_name = getattr(self.provider, "model_name", "Unknown")
        
        # Build tools list for injection into prompt
        tools_for_prompt = self.internal_tools if hasattr(self, 'internal_tools') else []
        
        # Memory system has been removed - always use solo prompt
        base_prompt = get_smart_agent_solo_prompt(
            model_name=model_name,
            tools=tools_for_prompt
        )
        
        # Store as custom system message so _rebuild_system_prompt_with_tools appends tools
        self._custom_system_message = base_prompt
        self.default_system_message = base_prompt

    
    def _load_smart_tools(self):
        """Load only essential Smart Agent tools - lean and focused."""
        # DO NOT load default tools - SmartAgent is lean by design
        # Curated tools include:
        # web_search, image_search, memory, notes, datetime, bash, cron scheduling
        
        # Get Smart Agent specific tools
        smart_tools = get_smart_agent_tools()  # datetime, notes, memory, bash, think
        
        for tool in smart_tools:
            # Skip 'think' tool - not in the required toolkit
            if tool.name == 'think':
                continue
            self.internal_tools.append(tool.schema)
            self.custom_tool_executors[tool.name] = tool.run
        
        # Add web_search from web tools
        from logicore.tools.web import WebSearchTool, ImageSearchTool
        web_tool = WebSearchTool()
        self.internal_tools.append(web_tool.schema)
        self.custom_tool_executors[web_tool.name] = web_tool.run
        
        # Add image_search tool for inline image responses
        image_tool = ImageSearchTool()
        self.internal_tools.append(image_tool.schema)
        self.custom_tool_executors[image_tool.name] = image_tool.run

        # Add cron scheduling tools (persistent + missed-job recovery + notifications)
        cron_tools = [
            AddCronJobTool(),
            ListCronJobsTool(),
            RemoveCronJobTool(),
            GetCronsTool(),
        ]
        for tool in cron_tools:
            self.internal_tools.append(tool.schema)
            self.custom_tool_executors[tool.name] = tool.run
        
        # IMPORTANT: Mark that tools are loaded and supported
        self.supports_tools = True
        
        # Update system message with loaded tools now
        self._update_system_message()
        
        # Rebuild system prompt with full tool schema dynamically
        self._rebuild_system_prompt_with_tools()
        
        if self.debug:
            tool_names = [t.get("function", {}).get("name") for t in self.internal_tools]
            print(f"[SmartAgent] Loaded tools: {tool_names}")
    
    def set_mode(self, mode: str, project_id: str = None):
        """Switch agent mode and regenerate system prompt with tools."""
        self.mode = mode
        
        # Memory system has been removed - project context is disabled
        if mode == SmartAgentMode.PROJECT and project_id:
            self.project_id = project_id
        else:
            self.project_context = None
        
        # Update system message with current tools
        self._update_system_message()
        
        # Rebuild with tools
        self._rebuild_system_prompt_with_tools()
        
        # Update all active sessions with new system message
        for session in self.sessions.values():
            if session.messages and session.messages[0]['role'] == 'system':
                session.messages[0]['content'] = self.default_system_message
    
    def create_project(self, project_id: str, title: str, goal: str = "",
                       environment: Dict[str, str] = None,
                       key_files: List[str] = None):
        """Create a new project - stub, memory has been removed."""
        if self.debug:
            print(f"[SmartAgent] create_project called but memory is disabled")
        return None
    
    def switch_to_project(self, project_id: str) -> Optional[ProjectContext]:
        """Switch to a specific project - stub, memory has been removed."""
        if self.debug:
            print(f"[SmartAgent] switch_to_project called but memory is disabled")
        self.project_id = project_id
        self.set_mode(SmartAgentMode.PROJECT, project_id)
        return None
    
    def switch_to_solo(self):
        """Switch to solo chat mode."""
        self.set_mode(SmartAgentMode.SOLO)
    
    def get_project_context_for_llm(self) -> str:
        """Get formatted project context for LLM injection - stub, memory removed."""
        return ""
    
    def list_projects(self):
        """List all available projects - stub, memory removed."""
        return []

    # --- Enhanced Chat (Memory Removed) ---

    async def chat(self, user_input: Union[str, List[Dict[str, Any]]],
                   session_id: str = "default", stream: bool = False, generate_walkthrough: bool = False, **kwargs) -> str:
        """
        Enhanced chat method.
        Memory system has been removed - calls parent chat directly.
        """
        try:
            response = await super().chat(user_input, session_id=session_id, stream=stream, generate_walkthrough=generate_walkthrough, **kwargs)
        except Exception as e:
            if self.debug:
                print(f"[SmartAgent] Error in chat: {e}")
            raise

        return response
    
    async def _maybe_capture_learning(self, user_input: str, response: str):
        """
        Stub - memory capture has been removed.
        """
        pass
    
    # --- Convenience Methods ---
    
    async def reason(self, problem: str, session_id: str = "default") -> str:
        """
        Explicitly request step-by-step reasoning for a problem.
        """
        prompt = f"""Please think through this problem step by step using the 'think' tool:

{problem}

After reasoning, provide your conclusion and solution."""
        
        return await self.chat(prompt, session_id)
    
    async def remember(self, memory_type: str, title: str, content: str,
                       tags: List[str] = None) -> str:
        """
        Stub - memory functionality has been removed.
        """
        return "Memory functionality has been removed from this version."
    
    async def recall(self, query: str, limit: int = 5):
        """
        Stub - memory functionality has been removed.
        """
        return []
    
    def status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            "mode": self.mode,
            "project_id": self.project_id,
            "project_title": self.project_context.title if self.project_context else None,
            "model": getattr(self.provider, "model_name", "Unknown"),
            "tools_loaded": len(self.internal_tools),
            "sessions_active": len(self.sessions),
            "memory_entries": 0  # Memory removed
        }
