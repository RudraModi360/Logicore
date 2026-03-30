import asyncio
import json
import os
import shutil
from typing import Dict, Any, List, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

class MCPClientManager:
    """
    Manages connections to external MCP servers defined in mcp.json.
    Acts as a bridge between the Agent and external MCP tools.
    """
    
    def __init__(self, config_path: str = "mcp.json", config: Dict[str, Any] = None):
        self.config_path = config_path
        self.config = config
        self.sessions: Dict[str, ClientSession] = {}
        self.server_tools_map: Dict[str, str] = {}  # tool_name -> server_name
        
        # Task management
        self.server_tasks: Dict[str, asyncio.Task] = {}
        self.server_stop_events: Dict[str, asyncio.Event] = {}
        
    async def load_config(self) -> Dict[str, Any]:
        """Load configuration from memory or mcp.json."""
        if self.config:
            return self.config

        if not os.path.exists(self.config_path):
            return {}
            
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[MCP Client] Error loading config: {e}")
            return {}

    async def _run_server_connection(self, server_name: str, params: StdioServerParameters, ready_event: asyncio.Event):
        """Background task to maintain connection to an MCP server."""
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.sessions[server_name] = session
                    
                    # Signal that connection is ready
                    ready_event.set()
                    
                    # Keep connection alive until stopped
                    if server_name in self.server_stop_events:
                        await self.server_stop_events[server_name].wait()
                        
        except Exception as e:
            # Only print error if it wasn't a requested stop
            is_stopping = server_name in self.server_stop_events and self.server_stop_events[server_name].is_set()
            if not is_stopping:
                print(f"[MCP Client] Connection error for {server_name}: {e}")
            
            # Ensure ready_event is set so main thread doesn't hang
            if not ready_event.is_set():
                ready_event.set()
                
            # Clean up session if it was set
            if server_name in self.sessions:
                del self.sessions[server_name]

    async def connect_to_servers(self):
        """Connect to all servers defined in mcp.json."""
        config = await self.load_config()
        servers = config.get("mcpServers", {})
        
        for server_name, server_config in servers.items():
            # Skip if it's the logicore server itself to avoid recursion
            if server_name.startswith("logicore"):
                continue
            
            # Skip if already connected
            if server_name in self.sessions:
                continue

            try:
                command = server_config.get("command")
                args = server_config.get("args", [])
                env = server_config.get("env", {})
                
                # Merge current env with config env
                full_env = os.environ.copy()
                full_env.update(env)
                
                # Resolve command path
                cmd_path = shutil.which(command) or command
                
                server_params = StdioServerParameters(
                    command=cmd_path,
                    args=args,
                    env=full_env
                )
                
                # Prepare events
                ready_event = asyncio.Event()
                stop_event = asyncio.Event()
                self.server_stop_events[server_name] = stop_event
                
                # Start background task
                task = asyncio.create_task(self._run_server_connection(server_name, server_params, ready_event))
                self.server_tasks[server_name] = task
                
                # Wait for interaction (timeout 10s)
                try:
                    await asyncio.wait_for(ready_event.wait(), timeout=10.0)
                    
                    if server_name in self.sessions:
                        print(f"[MCP Client] Connected to server: {server_name}")
                        
                        # List tools and map them
                        result = await self.sessions[server_name].list_tools()
                        print(f"[MCP Client] Found {len(result.tools)} tools from {server_name}")
                        for tool in result.tools:
                            self.server_tools_map[tool.name] = server_name
                    else:
                        print(f"[MCP Client] Failed to connect to {server_name} (Initialization failed)")
                        
                except asyncio.TimeoutError:
                    print(f"[MCP Client] Timeout connecting to {server_name}")
                    # Don't kill the task, it might just be slow, but we can't wait forever
                    
            except Exception as e:
                print(f"[MCP Client] Failed to setup {server_name}: {e}")

    async def get_tools(self) -> List[Dict[str, Any]]:
        """Get all tools from connected servers in OpenAI/Agentry schema format."""
        all_tools = []
        
        for server_name, session in self.sessions.items():
            try:
                result = await session.list_tools()
                for tool in result.tools:
                    logicore_tool = {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema
                        }
                    }
                    all_tools.append(logicore_tool)
            except Exception as e:
                print(f"[MCP Client] Error listing tools from {server_name}: {e}")
                
        return all_tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool on the appropriate external server."""
        server_name = self.server_tools_map.get(tool_name)
        if not server_name:
            raise ValueError(f"Tool {tool_name} not found in external servers")
            
        session = self.sessions.get(server_name)
        if not session:
            raise ValueError(f"Session for {server_name} is not active")
            
        result: CallToolResult = await session.call_tool(tool_name, arguments)
        
        # Process result content
        output = []
        if result.content:
            for item in result.content:
                if item.type == "text":
                    output.append(item.text)
                elif item.type == "image":
                    output.append(f"[Image: {item.mimeType}]")
                elif item.type == "resource":
                    output.append(f"[Resource: {item.uri}]")
        
        final_output = "\n".join(output)
        
        if result.isError:
            return {"success": False, "error": final_output}
        else:
            return {"success": True, "content": final_output}

    async def cleanup(self):
        """Close all connections."""
        # Signal all tasks to stop
        for event in self.server_stop_events.values():
            event.set()
        
        # Wait for all tasks to finish
        if self.server_tasks:
            await asyncio.gather(*self.server_tasks.values(), return_exceptions=True)
            
        self.server_tasks.clear()
        self.server_stop_events.clear()
        self.sessions.clear()
        self.server_tools_map.clear()
