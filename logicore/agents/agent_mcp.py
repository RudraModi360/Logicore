import json
import asyncio
import re
from typing import List, Dict, Any, Callable, Awaitable, Optional, Union, Set
from datetime import datetime
from logicore.providers.base import LLMProvider
from logicore.agents.agent import Agent, AgentSession
from logicore.tools import ALL_TOOL_SCHEMAS, DANGEROUS_TOOLS, APPROVAL_REQUIRED_TOOLS, execute_tool
from logicore.telemetry import TelemetryTracker
from logicore.simplemem import AgentrySimpleMem
import time
import logging

try:
    from logicore.mcp_client import MCPClientManager
except ImportError:
    # Fallback if running from different context
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from logicore.mcp_client import MCPClientManager

logger = logging.getLogger(__name__)


# Tool Search Schema - the only tool sent initially in deferred mode
TOOL_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_search_regex",
        "description": """Search for available tools using a regex pattern. Returns matching tool names and descriptions.
Use this FIRST to discover what tools are available before calling them.
After finding tools, they will be automatically loaded and available for use.

Tips for effective searching:
- Use simple patterns like 'file' to find file-related tools
- Use 'read|write|list' to find multiple related tools
- Use 'excel|sheet|workbook' for spreadsheet tools
- Use '.*' to list all available tools (use sparingly)""",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to match against tool names and descriptions. Case-insensitive. Examples: 'file', 'read|write', 'excel.*sheet'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tools to return (default: 10, max: 50)",
                    "default": 10
                }
            },
            "required": ["pattern"]
        }
    }
}


class MCPAgent(Agent):
    """
    MCP-Enhanced AI Agent that wraps the base Agent class.
    
    Adds MCP-specific capabilities on top of the core Agent:
    - MCP tool schema generation and export
    - Multi-session management with lifecycle callbacks
    - Stale session cleanup
    - MCP-compatible configuration generation
    - **Deferred Tool Loading**: Dynamically discover and load tools on-demand
      to avoid context size limits with many tools
    
    Features inherited from Agent:
    - Tool execution (internal, MCP, and custom)
    - Session management (AgentSession)
    - Context isolation between sessions
    - Dynamic tool registration
    - Enhanced callback system
    - Memory and telemetry support
    
    Deferred Tool Mode:
        When deferred_tools=True, tools are not sent upfront. Instead, the agent
        uses tool_search_regex to discover tools, which are then loaded on-demand.
        This allows scaling to hundreds of tools without hitting context limits.
    """
    
    def __init__(
        self,
        provider: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        endpoint: str = None,
        system_message: str = "You are a helpful AI assistant with access to various tools.",
        debug: bool = False,
        telemetry: bool = False,
        memory: bool = False,
        max_iterations: int = 40,
        session_timeout: int = 3600,  # 1 hour default
        mcp_config_path: str = None,
        mcp_config: Dict[str, Any] = None,
        deferred_tools: bool = False,
        tool_threshold: int = 15  # Auto-enable deferred mode if tools exceed this
    ):
        """
        Initialize MCPAgent.
        
        Args:
            provider: LLM provider (instance or string: "ollama", "groq", "gemini", "azure", "openai")
            model: Model name
            api_key: API key for cloud providers
            endpoint: Custom endpoint (for Azure, etc.)
            system_message: System prompt for the agent
            debug: Enable debug logging
            telemetry: Enable telemetry tracking
            memory: Enable memory/context middleware
            max_iterations: Max tool execution iterations per chat
            session_timeout: Session timeout in seconds
            mcp_config_path: Path to MCP config file
            mcp_config: MCP config dict (alternative to file)
            deferred_tools: Enable deferred tool loading (dynamic discovery)
            tool_threshold: Auto-enable deferred mode if tool count exceeds this
        """
        # Initialize base Agent (tools=False initially if deferred, we'll manage them)
        super().__init__(
            llm=provider,
            model=model,
            api_key=api_key,
            endpoint=endpoint,
            system_message=system_message,
            role="mcp",
            debug=debug,
            telemetry=telemetry,
            memory=memory,
            max_iterations=max_iterations,
            tools=not deferred_tools  # Only load default tools if not deferred
        )
        
        # MCP-specific configuration
        self.session_timeout = session_timeout
        self.mcp_config_path = mcp_config_path
        self.mcp_config = mcp_config
        self._mcp_initialized = False
        
        # Deferred tool loading configuration
        self.deferred_tools = deferred_tools
        self.tool_threshold = tool_threshold
        self._tool_registry: Dict[str, Dict[str, Any]] = {}  # name -> full schema
        self._loaded_tools: Set[str] = set()  # Tools currently loaded for use
        self._auto_deferred = False  # Tracks if deferred mode was auto-enabled
        
        # If deferred mode, populate the registry with default tools
        if deferred_tools:
            self._register_default_tools_deferred()
        
        # Session lifecycle callbacks
        self.on_session_created: Optional[Callable[[str], None]] = None
        self.on_session_destroyed: Optional[Callable[[str], None]] = None
    
    def _register_default_tools_deferred(self):
        """Register default tools in the deferred registry."""
        for tool in ALL_TOOL_SCHEMAS:
            name = tool.get("function", {}).get("name")
            if name:
                self._tool_registry[name] = tool
        if self.debug:
            print(f"[MCPAgent] 📦 Registered {len(self._tool_registry)} tools in deferred registry")
    
    def _search_tools(self, pattern: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for tools matching a regex pattern.
        
        Args:
            pattern: Regex pattern (case-insensitive)
            limit: Maximum results to return
            
        Returns:
            List of matching tools with name, description, and danger info
        """
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return [{"error": f"Invalid regex pattern: {e}"}]
        
        matches = []
        for name, schema in self._tool_registry.items():
            func = schema.get("function", {})
            tool_name = func.get("name", "")
            description = func.get("description", "")
            
            # Match against name or description
            if regex.search(tool_name) or regex.search(description):
                # Return compact info, not full schema
                matches.append({
                    "name": tool_name,
                    "description": (description[:150] + "...") if len(description) > 150 else description,
                    "dangerous": tool_name in DANGEROUS_TOOLS,
                    "requires_approval": tool_name in (DANGEROUS_TOOLS + APPROVAL_REQUIRED_TOOLS),
                    "loaded": tool_name in self._loaded_tools
                })
                
                # Auto-load the tool when found
                self._loaded_tools.add(tool_name)
                
                if len(matches) >= min(limit, 50):
                    break
        
        return matches
    
    def _execute_tool_search(self, pattern: str, limit: int = 10) -> Dict[str, Any]:
        """Execute the tool_search_regex tool."""
        matches = self._search_tools(pattern, limit)
        
        if not matches:
            return {
                "status": "no_matches",
                "message": f"No tools found matching pattern '{pattern}'",
                "suggestion": "Try a broader pattern like '.*' or simpler terms like 'file', 'read', 'web'"
            }
        
        # Mark found tools as loaded
        newly_loaded = []
        for match in matches:
            if not match.get("loaded"):
                newly_loaded.append(match["name"])
        
        return {
            "status": "success",
            "pattern": pattern,
            "total_matches": len(matches),
            "tools": matches,
            "newly_loaded": len(newly_loaded),
            "message": f"Found {len(matches)} tools. They are now available for use."
        }
    
    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """
        Get tools for the current mode.
        
        In deferred mode: Returns only tool_search_regex + any loaded tools
        In normal mode: Returns all tools (inherited behavior)
        """
        if not self.deferred_tools:
            # Check if we should auto-enable deferred mode
            all_tools = await super().get_all_tools()
            if len(all_tools) > self.tool_threshold and not self._auto_deferred:
                if self.debug:
                    print(f"[MCPAgent] ⚠️ Tool count ({len(all_tools)}) exceeds threshold ({self.tool_threshold})")
                    print(f"[MCPAgent] 🔄 Auto-enabling deferred tool mode...")
                
                # Switch to deferred mode
                self.deferred_tools = True
                self._auto_deferred = True
                
                # Move existing tools to registry
                for tool in all_tools:
                    name = tool.get("function", {}).get("name")
                    if name:
                        self._tool_registry[name] = tool
                
                # Clear internal tools from parent (we'll manage them via registry)
                self.internal_tools = []
                
                return await self.get_all_tools()  # Recurse with deferred mode
            
            return all_tools
        
        # Deferred mode: Return search tool + loaded tools
        tools = [TOOL_SEARCH_SCHEMA]
        
        for tool_name in self._loaded_tools:
            if tool_name in self._tool_registry:
                tools.append(self._tool_registry[tool_name])
        
        return tools
    
    async def init_mcp_servers(self):
        """
        Initialize MCP servers from config.
        
        Call this method explicitly after creating the agent if you provided mcp_config_path or mcp_config.
        This must be called from within an async context (e.g., inside an async function).
        
        In deferred mode, MCP tools are registered in the deferred registry.
        
        Example:
            agent = MCPAgent(..., mcp_config_path="mcp.json")
            await agent.init_mcp_servers()  # Initialize MCP servers
        """
        if self._mcp_initialized:
            if self.debug:
                print("[MCPAgent] ℹ️ MCP servers already initialized")
            return
        
        if not (self.mcp_config_path or self.mcp_config):
            if self.debug:
                print("[MCPAgent] ℹ️ No MCP config provided, skipping initialization")
            return
        
        try:
            await self.add_mcp_server(
                config_path=self.mcp_config_path,
                config=self.mcp_config
            )
            
            # In deferred mode, register MCP tools in the deferred registry
            if self.deferred_tools or self._auto_deferred:
                for manager in self.mcp_managers:
                    mcp_tools = await manager.get_tools()
                    for tool in mcp_tools:
                        name = tool.get("function", {}).get("name")
                        if name:
                            self._tool_registry[name] = tool
                    if self.debug:
                        print(f"[MCPAgent] 📦 Registered {len(mcp_tools)} MCP tools in deferred registry")
            
            self._mcp_initialized = True
            if self.debug:
                print("[MCPAgent] ✅ MCP servers initialized successfully")
                print(f"[MCPAgent] 📊 Total tools in registry: {len(self._tool_registry)}")
        except Exception as e:
            if self.debug:
                print(f"[MCPAgent] ⚠️ Failed to initialize MCP servers: {e}")
            raise
    
    async def _lazy_init_mcp(self):
        """Internal: Initialize MCP servers if not already done (lazy initialization)."""
        if not self._mcp_initialized and (self.mcp_config_path or self.mcp_config):
            await self.init_mcp_servers()
    
    async def _init_mcp_servers(self):
        """Deprecated: Use init_mcp_servers() instead."""
        await self.init_mcp_servers()
    
    async def _execute_tool(self, name: str, args: Dict, session_id: str) -> Any:
        """
        Execute a tool, with special handling for tool_search_regex.
        
        Overrides parent to handle:
        - tool_search_regex: Internal dynamic tool discovery
        - Deferred tool loading and execution
        """
        # Handle the special tool_search_regex tool
        if name == "tool_search_regex":
            pattern = args.get("pattern", ".*")
            limit = args.get("limit", 10)
            result = self._execute_tool_search(pattern, limit)
            
            if self.debug:
                loaded_count = len(self._loaded_tools)
                registry_count = len(self._tool_registry)
                print(f"[MCPAgent] 🔍 Tool search: '{pattern}' -> {result.get('total_matches', 0)} matches")
                print(f"[MCPAgent] 📊 Loaded: {loaded_count}/{registry_count} tools")
            
            return result
        
        # For deferred mode, ensure the tool is loaded before execution
        if (self.deferred_tools or self._auto_deferred) and name in self._tool_registry:
            self._loaded_tools.add(name)
        
        # Delegate to parent for actual tool execution
        return await super()._execute_tool(name, args, session_id)
    
    def set_session_callbacks(
        self,
        on_session_created: Optional[Callable[[str], None]] = None,
        on_session_destroyed: Optional[Callable[[str], None]] = None,
    ):
        """Set callbacks for session lifecycle events."""
        self.on_session_created = on_session_created
        self.on_session_destroyed = on_session_destroyed
        if self.debug:
            print("[MCPAgent] Session callbacks registered")
    
    def register_tool_deferred(self, schema: Dict[str, Any], preload: bool = False):
        """
        Register a tool in the deferred registry.
        
        Args:
            schema: Tool schema dict with 'function' key
            preload: If True, also add to loaded tools (available immediately)
        """
        name = schema.get("function", {}).get("name")
        if name:
            self._tool_registry[name] = schema
            if preload:
                self._loaded_tools.add(name)
            if self.debug:
                state = "loaded" if preload else "deferred"
                print(f"[MCPAgent] 📦 Registered tool '{name}' ({state})")
    
    def preload_tools(self, tool_names: List[str]):
        """
        Pre-load specific tools so they're available immediately.
        
        Useful for tools you know will be needed frequently.
        
        Args:
            tool_names: List of tool names to preload
        """
        for name in tool_names:
            if name in self._tool_registry:
                self._loaded_tools.add(name)
                if self.debug:
                    print(f"[MCPAgent] ⚡ Preloaded tool: {name}")
            else:
                if self.debug:
                    print(f"[MCPAgent] ⚠️ Tool not found in registry: {name}")
    
    def get_registry_stats(self) -> Dict[str, Any]:
        """Get statistics about the tool registry."""
        return {
            "total_registered": len(self._tool_registry),
            "loaded": len(self._loaded_tools),
            "deferred": len(self._tool_registry) - len(self._loaded_tools),
            "deferred_mode": self.deferred_tools or self._auto_deferred,
            "auto_deferred": self._auto_deferred,
            "threshold": self.tool_threshold,
            "tool_names": list(self._tool_registry.keys())
        }
    
    def register_tool_from_function(self, func: Callable):
        """
        Register a function as a tool, adding to deferred registry if in deferred mode.
        
        Overrides parent to support deferred tool loading.
        """
        # Call parent to generate schema
        super().register_tool_from_function(func)
        
        # In deferred mode, also add to registry and preload it
        if self.deferred_tools or self._auto_deferred:
            # Get the just-added schema from internal_tools
            if self.internal_tools:
                schema = self.internal_tools[-1]  # Last added
                name = schema.get("function", {}).get("name")
                if name:
                    self._tool_registry[name] = schema
                    self._loaded_tools.add(name)  # Custom tools are preloaded
                    if self.debug:
                        print(f"[MCPAgent] 📦 Custom tool '{name}' added to registry (preloaded)")
    
    def create_session(
        self, 
        session_id: str, 
        system_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentSession:
        """
        Create a new client session with isolated context.
        Wrapper around Agent's get_session with callback support.
        """
        session = self.get_session(session_id)
        
        # Update system message if provided
        if system_message:
            session.messages[0]["content"] = system_message
        
        # Add metadata if provided
        if metadata:
            session.metadata = metadata
        
        # Trigger callback
        if self.on_session_created:
            self.on_session_created(session_id)
        
        if self.debug:
            print(f"[MCPAgent] ✅ Created session: {session_id}")
        
        return session
    
    def destroy_session(self, session_id: str) -> bool:
        """Destroy a session and clean up resources."""
        if session_id not in self.sessions:
            return False
        
        del self.sessions[session_id]
        
        if self.on_session_destroyed:
            self.on_session_destroyed(session_id)
        
        if self.debug:
            print(f"[MCPAgent] ✅ Destroyed session: {session_id}")
        
        return True
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions with their summaries."""
        summaries = []
        for session_id, session in self.sessions.items():
            summaries.append({
                "session_id": session.session_id,
                "message_count": len(session.messages),
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "metadata": session.metadata
            })
        return summaries
    
    def cleanup_stale_sessions(self) -> int:
        """Remove sessions that have exceeded the timeout period."""
        now = datetime.now()
        stale_sessions = []
        
        for session_id, session in self.sessions.items():
            time_diff = (now - session.last_activity).total_seconds()
            if time_diff > self.session_timeout:
                stale_sessions.append(session_id)
        
        for session_id in stale_sessions:
            self.destroy_session(session_id)
        
        if self.debug and stale_sessions:
            print(f"[MCPAgent] 🧹 Cleaned up {len(stale_sessions)} stale sessions")
        
        return len(stale_sessions)
    
    async def list_mcp_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Generate MCP-compatible tool schema from registered tools.
        
        Returns:
            List of tool definitions in MCP format with name, description, 
            input schema, and metadata about danger level.
        """
        mcp_tools = []
        all_tools = await self.get_all_tools()
        
        for tool in all_tools:
            tool_func = tool.get("function", {})
            tool_name = tool_func.get("name")
            
            mcp_tool = {
                "name": tool_name,
                "description": tool_func.get("description", ""),
                "input_schema": tool_func.get("parameters", {}),
                "dangerous": tool_name in DANGEROUS_TOOLS,
                "requires_approval": tool_name in (DANGEROUS_TOOLS + APPROVAL_REQUIRED_TOOLS)
            }
            mcp_tools.append(mcp_tool)
        
        return mcp_tools
    
    async def export_mcp_config(self, filepath: str = "mcp_tools.json"):
        """
        Export MCP tool configuration to a JSON file.
        
        Args:
            filepath: Output file path for MCP config
        """
        config = {
            "version": "1.0",
            "tools": await self.list_mcp_tools_schema(),
            "metadata": {
                "provider": self.provider.__class__.__name__,
                "model": self.model_name,
                "max_iterations": self.max_iterations,
                "session_timeout": self.session_timeout,
                "exported_at": datetime.now().isoformat()
            }
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        if self.debug:
            print(f"[MCPAgent] ✅ Exported configuration to {filepath}")
    
    async def chat(
        self, 
        user_input: str, 
        session_id: str = "default",
        create_if_missing: bool = True,
        stream: bool = False,
        streaming_funct: Optional[Callable[[str], None]] = None,
        generate_walkthrough: bool = True,
        **kwargs
    ) -> Optional[str]:
        """
        Process a chat message within a specific session context.
        
        Wrapper around the parent Agent.chat() method with MCP-specific 
        enhancements for session management.
        
        Args:
            user_input: The user's message
            session_id: The session identifier
            create_if_missing: Whether to create the session if it doesn't exist
            stream: Whether to stream the response
            streaming_funct: Callback for streaming tokens
            **kwargs: Additional arguments passed to parent chat()
        
        Returns:
            The final assistant response or None if max iterations reached
        """
        # Lazy initialize MCP servers on first chat if configured
        await self._lazy_init_mcp()
        
        # Create session if missing
        if session_id not in self.sessions and create_if_missing:
            self.create_session(session_id)
        
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} does not exist")
        
        # Call parent chat method
        response = await super().chat(
            user_input,
            session_id=session_id,
            stream=stream,
            streaming_funct=streaming_funct,
            generate_walkthrough=generate_walkthrough,
            **kwargs
        )
        
        return response
    
    def get_session_history(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get the full message history for a session."""
        session = self.get_session(session_id)
        return session.messages if session else None
    
    def clear_session_history(self, session_id: str, keep_system: bool = True) -> bool:
        """Clear the conversation history for a session."""
        session = self.get_session(session_id)
        if session:
            session.clear_history(keep_system)
            return True
        return False
