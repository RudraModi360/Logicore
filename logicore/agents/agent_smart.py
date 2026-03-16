import json
import asyncio
from typing import List, Dict, Any, Optional, Union, Callable
from datetime import datetime
from logicore.providers.base import LLMProvider
from logicore.agents.agent import Agent, AgentSession
from logicore.memory.project_memory import (
    ProjectMemory, ProjectContext, MemoryType, MemoryEntry,
    get_project_memory
)
from logicore.tools.agent_tools import (
    get_smart_agent_tools, get_smart_agent_tool_schemas,
    DateTimeTool, NotesTool, MemoryTool, SmartBashTool, ThinkTool
)
from logicore.tools.cron_tools import AddCronJobTool, ListCronJobsTool, RemoveCronJobTool, GetCronsTool
from logicore.config.prompts import (
    get_system_prompt,
    get_smart_agent_solo_prompt,
    get_smart_agent_project_prompt
)


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
            capabilities=capabilities,
            skills=skills,
            workspace_root=workspace_root
        )
        
        # Smart Agent specific
        self.mode = mode
        self.project_id = project_id
        self.project_memory = get_project_memory()
        self.project_context: Optional[ProjectContext] = None
        
        # Load project context if in project mode
        if mode == SmartAgentMode.PROJECT and project_id:
            self.project_context = self.project_memory.get_project(project_id)
        
        # Set appropriate system message
        self._update_system_message()
        
        # Load Smart Agent tools
        self._load_smart_tools()
    
    def _update_system_message(self):
        """Update system message based on mode and project context."""
        model_name = getattr(self.provider, "model_name", "Unknown")
        
        # Build tools list for injection into prompt
        tools_for_prompt = self.internal_tools if hasattr(self, 'internal_tools') else []
        
        if self.mode == SmartAgentMode.PROJECT and self.project_context:
            # Build project context dict for the prompt function
            project_dict = {
                "title": self.project_context.title,
                "goal": self.project_context.goal,
                "project_id": self.project_context.project_id,
                "environment": self.project_context.environment or {},
                "key_files": self.project_context.key_files or [],
                "current_focus": self.project_context.current_focus,
            }
            base_prompt = get_smart_agent_project_prompt(
                model_name=model_name,
                project_context=project_dict,
                tools=tools_for_prompt
            )
        else:
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
        
        if mode == SmartAgentMode.PROJECT:
            if project_id:
                self.project_id = project_id
                self.project_context = self.project_memory.get_project(project_id)
            elif self.project_id:
                self.project_context = self.project_memory.get_project(self.project_id)
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
                       key_files: List[str] = None) -> ProjectContext:
        """Create a new project and optionally switch to project mode."""
        project = self.project_memory.create_project(
            project_id=project_id,
            title=title,
            goal=goal,
            environment=environment,
            key_files=key_files
        )
        
        if self.debug:
            print(f"[SmartAgent] Created project: {title} ({project_id})")
        
        return project
    
    def switch_to_project(self, project_id: str) -> Optional[ProjectContext]:
        """Switch to a specific project."""
        project = self.project_memory.get_project(project_id)
        if project:
            self.project_id = project_id
            self.project_context = project
            self.set_mode(SmartAgentMode.PROJECT, project_id)
            return project
        return None
    
    def switch_to_solo(self):
        """Switch to solo chat mode."""
        self.set_mode(SmartAgentMode.SOLO)
    
    def get_project_context_for_llm(self) -> str:
        """Get formatted project context for LLM injection."""
        if not self.project_id:
            return ""
        return self.project_memory.export_project_context(self.project_id)
    
    def list_projects(self) -> List[ProjectContext]:
        """List all available projects."""
        return self.project_memory.list_projects()

    # --- Enhanced Chat with Memory and Learning ---

    async def chat(self, user_input: Union[str, List[Dict[str, Any]]],
                   session_id: str = "default", stream: bool = False, generate_walkthrough: bool = False, **kwargs) -> str:
        """
        Enhanced chat with automatic capture of significant learnings.

        Memory is now explicit and on-demand (RAG-based via the 'memory' tool),
        NOT auto-injected at chat start. This prevents casual chat from polluting context.
        
        Learnings are auto-captured only for significant results (solutions, patterns, insights).
        Casual conversations (greetings, simple confirmations) are ignored.
        """
        # Get project memories if in project mode and inject as project context
        if self.mode == SmartAgentMode.PROJECT and self.project_id:
            session = self.get_session(session_id)
            project_context = self.get_project_context_for_llm()
            if project_context and session.messages:
                if session.messages[0]['role'] == 'system':
                    base = self.default_system_message
                    session.messages[0]['content'] = base + "\n\n" + project_context

        try:
            # Call parent chat - no memory judgment, just explicit RAG via tool
            response = await super().chat(user_input, session_id=session_id, stream=stream, generate_walkthrough=generate_walkthrough, **kwargs)
        except Exception as e:
            if self.debug:
                print(f"[SmartAgent] Error in chat: {e}")
            raise

        # Auto-capture significant learnings only (casual chat filtered out)
        if self.mode == SmartAgentMode.PROJECT and response:
            await self._maybe_capture_learning(user_input, response)

        return response
    
    async def _maybe_capture_learning(self, user_input: str, response: str):
        """
        Heuristically capture SIGNIFICANT learnings only - filter out casual chat.
        
        This prevents random 'hello' or 'remind me to X' from polluting memory.
        Only captures when response contains clear value signals:
        - Solutions, fixes, approaches, insights
        - Explanations, patterns, best practices
        - Structured decisions or recommendations
        
        Casual responses are ignored and not stored.
        """
        # 1. Filter out casual/low-value conversations
        casual_indicators = [
            "hello", "hi there", "how are you", "what's up", "thanks", 
            "okay", "sounds good", "got it", "sure", "no problem",
            "bye", "goodbye", "take care", "see you",
            "nice to meet", "pleasure to meet"
        ]
        response_lower = response.lower()
        
        # If response is mostly casual, skip learning capture
        casual_words = sum(1 for indicator in casual_indicators if indicator in response_lower)
        if casual_words >= 1 and len(response.split()) < 20:
            # Casual response with few words and greeting-like content
            if self.debug:
                print(f"[SmartAgent] ⏭️  Skipped memory capture - casual conversation detected")
            return
        
        # 2. Check for valid learning indicators in response
        learning_indicators = [
            "the solution is", "the fix is", "solved by", "the approach is",
            "remember to", "note that", "important:", "key insight",
            "best practice", "the pattern is", "always use", "never use",
            "recommendation", "suggested", "strategy", "technique", "method",
            "way to", "instead of", "problem:", "issue:", "found that"
        ]
        
        for indicator in learning_indicators:
            if indicator in response_lower:
                # Found a potential learning - store it
                try:
                    # Extract a snippet around the indicator
                    idx = response_lower.find(indicator)
                    start = max(0, idx - 50)
                    end = min(len(response), idx + 200)
                    snippet = response[start:end].strip()
                    
                    # Store as learning
                    self.project_memory.add_memory(
                        memory_type=MemoryType.LEARNING,
                        title=f"Learning from conversation",
                        content=snippet,
                        tags=["auto-captured"],
                        project_id=self.project_id
                    )
                    
                    if self.debug:
                        print(f"[SmartAgent] Auto-captured learning: {snippet[:50]}...")
                    
                    break  # Only capture one learning per response
                except Exception as e:
                    if self.debug:
                        print(f"[SmartAgent] Failed to capture learning: {e}")
    
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
        Store a memory directly.
        """
        mem_type = MemoryType(memory_type)
        entry = self.project_memory.add_memory(
            memory_type=mem_type,
            title=title,
            content=content,
            tags=tags,
            project_id=self.project_id if self.mode == SmartAgentMode.PROJECT else None
        )
        return f"Stored memory: [{mem_type.value}] {title} (ID: {entry.id})"
    
    async def recall(self, query: str, limit: int = 5) -> List[MemoryEntry]:
        """
        Search memories.
        """
        return self.project_memory.search_memories(
            query=query,
            project_id=self.project_id if self.mode == SmartAgentMode.PROJECT else None,
            limit=limit
        )
    
    def status(self) -> Dict[str, Any]:
        """Get current agent status."""
        return {
            "mode": self.mode,
            "project_id": self.project_id,
            "project_title": self.project_context.title if self.project_context else None,
            "model": getattr(self.provider, "model_name", "Unknown"),
            "tools_loaded": len(self.internal_tools),
            "sessions_active": len(self.sessions),
            "memory_entries": len(self.project_memory.get_memories(
                project_id=self.project_id, 
                limit=1000
            )) if self.project_id else 0
        }
