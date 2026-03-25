import time
import json
import inspect
import asyncio
import re
from typing import List, Dict, Any, Callable, Awaitable, Optional, Union, Tuple, get_type_hints
from datetime import datetime
from logicore.providers.base import LLMProvider
from logicore.providers.gateway import ProviderGateway, get_gateway_for_provider
from logicore.tools import ALL_TOOL_SCHEMAS, DANGEROUS_TOOLS, APPROVAL_REQUIRED_TOOLS, SAFE_TOOLS, execute_tool
from logicore.config.prompts import get_system_prompt
from logicore.skills import Skill, SkillLoader
from logicore.telemetry import TelemetryTracker
from logicore.simplemem import AgentrySimpleMem
import logging

logger = logging.getLogger(__name__)

import sys
import os
import tempfile
from urllib.parse import urlparse
from logicore.mcp_client import MCPClientManager
class AgentSession:
    """Represents a conversation session."""
    def __init__(self, session_id: str, system_message: str):
        self.session_id = session_id
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.metadata: Dict[str, Any] = {}
        self.files: Dict[str, str] = {} # VFS: Filename -> Content
    
    def add_message(self, message: Dict[str, Any]):
        self.messages.append(message)
        self.last_activity = datetime.now()
    
    def clear_history(self, keep_system: bool = True):
        if keep_system:
            self.messages = [msg for msg in self.messages if msg.get('role') == 'system']
        else:
            self.messages = []
        self.last_activity = datetime.now()

class Agent:
    """
    A unified, modular AI Agent that supports:
    - Internal tools (filesystem, web, etc.)
    - External MCP tools (Excel, etc.)
    - Multi-session management
    - Custom tool registration
    - Persistent Memory Middleware
    """
    
    def __init__(
        self,
        llm: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        endpoint: str = None,
        system_message: str = None,
        role: str = "general",
        debug: bool = False,
        tools: list = [],
        max_iterations: int = 40,
        telemetry: bool = False,
        memory: bool = False,
        context_compression: bool = False,
        skills: list = None,
        workspace_root: str = None
    ):
        if isinstance(llm, str):
            self.provider = self._create_provider(llm, model, api_key, endpoint)
            self.model_name = model or "Default Model"
        else:
            self.provider = llm
            self.model_name = getattr(llm, "model_name", "Custom Provider")

        self.debug = debug
        
        # Initialize the provider gateway for unified interface
        self.gateway: ProviderGateway = get_gateway_for_provider(self.provider)
        
        self.llm = self.provider
        try:
            setattr(self.provider, "debug", self.debug)
        except Exception:
            pass

        self.default_system_message = system_message or get_system_prompt(self.model_name, role)
        self._custom_system_message = system_message  # Store original if user provided one
        self.max_iterations = max_iterations
        self.role = role
        
        self.telemetry_enabled = telemetry
        self.telemetry_tracker = TelemetryTracker(enabled=telemetry)
        
        self.memory_enabled = memory
        from logicore.simplemem import AgentrySimpleMem
        self.simplemem = AgentrySimpleMem(user_id=self.role, session_id="default", debug=self.debug) if memory else None

        # Context compression middleware (summarizes old messages when context grows long)
        self.context_compression = context_compression
        self.context_middleware = None
        if context_compression:
            from logicore.memory.context_middleware import ContextMiddleware
            self.context_middleware = ContextMiddleware(self.provider)

        # Tool Management
        self.internal_tools = []  # List of schemas
        self.mcp_managers: List[MCPClientManager] = []
        self.custom_tool_executors: Dict[str, Callable] = {}
        self.disabled_tools = set()
        
        # Skills Management
        self.skills: List[Skill] = []
        self.skill_tool_executors: Dict[str, Callable] = {}  # skill tool name -> executor
        self.workspace_root = workspace_root
        
        # Session Management
        self.sessions: Dict[str, AgentSession] = {}
        
        # Execution Tracking
        self.execution_log: List[str] = []
        
        # Tool support flag - set when load_default_tools is called
        self.supports_tools = False
        self.tools_disabled_reason = None  # Optional message explaining why tools are disabled
        
        # Handle tools parameter
        if tools is True:
            # If tools=True, load all default tools
            self.load_default_tools()
        elif isinstance(tools, list) and len(tools) > 0:
            # If tools is a list, register those specific tools (don't load defaults)
            for tool in tools:
                if callable(tool):
                    # Register callable functions as custom tools
                    self.register_tool_from_function(tool)
                elif isinstance(tool, dict):
                    # If dict schema is provided, add it directly
                    self.internal_tools.append(tool)
                    tool_name = tool.get("function", {}).get("name")
                    if tool_name:
                        self.custom_tool_executors[tool_name] = tool  # Store schema reference
            
            # Mark tools as supported
            if len(self.internal_tools) > 0:
                self.supports_tools = True
                self._rebuild_system_prompt_with_tools()
                if self.debug:
                    print(f"[Agent] Loaded {len(self.internal_tools)} custom tool(s) (default tools NOT loaded)")
        
        # Load skills if provided
        if skills:
            self.load_skills(skills)
        
        # Callbacks
        self.callbacks = {
            "on_tool_start": None,
            "on_tool_end": None,
            "on_tool_approval": None,
            "on_final_message": None,
            "on_token": None  # For streaming token updates
        }
        
        # Tool Approval Control
        self.auto_approve_all = False  # Set to True to skip all approval checks

    @property
    def system_prompt(self) -> str:
        """Access the current system prompt being used by the agent."""
        return self.default_system_message

    @property
    def telemetry(self) -> dict:
        """Access telemetry data directly. Returns summary of all sessions or the single active session."""
        if not self.telemetry_enabled:
            return {"error": "Telemetry is not enabled. Set telemetry=True when initializing the Agent."}
        
        session_ids = self.telemetry_tracker.get_session_ids()
        if len(session_ids) == 1:
            return self.telemetry_tracker.get_session_summary(session_ids[0])
        elif len(session_ids) == 0:
            return {"message": "No telemetry data recorded yet."}
        return self.telemetry_tracker.get_total_summary()

    def _rebuild_system_prompt_with_tools(self):
        """Regenerate the system prompt to include currently registered tools and skill instructions."""
        # Format tools from internal_tools schemas with full parameter details
        from logicore.config.prompts import _format_tools
        tool_prompt_cap = 60
        visible_tools = self.internal_tools[:tool_prompt_cap]
        hidden_count = max(0, len(self.internal_tools) - len(visible_tools))
        tools_section = _format_tools(visible_tools)
        if hidden_count > 0:
            tools_section += (
                f"\n\n- Note: {hidden_count} additional tools are available at runtime "
                "but omitted from prompt docs for context efficiency."
            )
        
        # Build skill instructions section
        skills_section = self._build_skills_prompt_section()
        
        # Decide whether to use custom or auto-generated system message
        if self._custom_system_message:
            # User provided a custom system message - replace known tools sections or append
            if "<available_tools>" in self._custom_system_message:
                self.default_system_message = re.sub(
                    r'<available_tools>[\s\S]*?</available_tools>',
                    tools_section.strip() if tools_section else "",
                    self._custom_system_message
                )
            elif "## Available Tools" in self._custom_system_message:
                self.default_system_message = re.sub(
                    r'## Available Tools[\s\S]*?(?=\n## |\Z)',
                    tools_section.strip() + "\n",
                    self._custom_system_message
                )
            else:
                # No existing tools section, just append
                self.default_system_message = self._custom_system_message + tools_section
            
            # Append skill instructions
            if skills_section:
                self.default_system_message += skills_section
            
            if self.debug:
                print(f"[Agent] System prompt (custom + tools + skills): {len(self._custom_system_message)} chars + tools + {len(self.skills)} skills")
        else:
            # Use auto-generated system prompt with tools
            from logicore.config.prompts import get_system_prompt
            base_prompt = get_system_prompt(
                model_name=self.model_name, 
                role=self.role,
                tools=self.internal_tools  # Pass schemas, the function will format them
            )
            self.default_system_message = base_prompt
            
            # Append skill instructions
            if skills_section:
                self.default_system_message += skills_section
            
            if self.debug:
                print(f"[Agent] System prompt (auto-generated with tools + {len(self.skills)} skills): {len(self.default_system_message)} chars")

        max_prompt_chars = 36000
        if len(self.default_system_message) > max_prompt_chars:
            self.default_system_message = (
                self.default_system_message[:max_prompt_chars]
                + "\n\n[System prompt truncated for context efficiency.]"
            )
            if self.debug:
                print(f"[Agent] ⚠️ System prompt exceeded {max_prompt_chars} chars and was truncated")
        
        # Update system message in all existing sessions
        for session in self.sessions.values():
            if session.messages and session.messages[0].get("role") == "system":
                session.messages[0]["content"] = self.default_system_message
        
        if self.debug:
            print(f"[Agent] System prompt updated with {len(self.internal_tools)} tools and {len(self.skills)} skills")

    def _create_provider(self, provider_name: str, model: str, api_key: str, endpoint: str = None) -> LLMProvider:
        """
        Factory method to create providers from strings.
        The returned provider instance will be wrapped by a ProviderGateway in __init__.
        
        When adding a new provider:
        1. Create the provider class in logicore/providers/
        2. Add it here in the factory
        3. Create a corresponding gateway class in logicore/providers/gateway.py
        4. Update the get_gateway_for_provider() function in gateway.py
        """
        provider_name = provider_name.lower()
        
        if provider_name == "ollama":
            from logicore.providers.ollama_provider import OllamaProvider
            return OllamaProvider(model_name=model or "gpt-oss:20b-cloud")
            
        elif provider_name == "groq":
            from logicore.providers.groq_provider import GroqProvider
            return GroqProvider(model_name=model or "llama-3.3-70b-versatile", api_key=api_key)
            
        elif provider_name == "gemini":
            from logicore.providers.gemini_provider import GeminiProvider
            return GeminiProvider(model_name=model or "gemini-pro", api_key=api_key)
            
        elif provider_name == "azure":
            from logicore.providers.azure_provider import AzureProvider
            return AzureProvider(model_name=model, api_key=api_key, endpoint=endpoint)
        
        elif provider_name == "openai":
            from logicore.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(model_name=model or "gpt-4", api_key=api_key)
            
        else:
            raise ValueError(f"Unknown provider: {provider_name}. Supported: 'ollama', 'groq', 'gemini', 'azure', 'openai'.")


    # --- Skill Management ---

    def _build_skills_prompt_section(self) -> str:
        """Build the skills instructions section for the system prompt."""
        if not self.skills:
            return ""
        
        blocks = []
        for skill in self.skills:
            block = f"### Skill: {skill.name}\n"
            block += f"_{skill.description}_\n\n"
            block += skill.instructions
            blocks.append(block)
        
        skills_str = "\n\n---\n\n".join(blocks)
        return f"\n\n## Active Skills\n{skills_str}"

    def _parse_tool_arguments(self, name: str, raw_args: Any) -> Tuple[Dict[str, Any], Optional[str]]:
        """Parse tool-call arguments into a JSON object and return parse error if invalid."""
        if raw_args is None:
            return {}, None

        if isinstance(raw_args, dict):
            return raw_args, None

        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                return {}, f"Tool '{name}' received invalid JSON arguments: {str(exc)}"

            if not isinstance(parsed, dict):
                return {}, f"Tool '{name}' arguments must be a JSON object (dictionary), got {type(parsed).__name__}."

            return parsed, None

        return {}, f"Tool '{name}' arguments must be a dict or JSON object string, got {type(raw_args).__name__}."

    def _normalize_tool_result(self, tool_name: str, result: Any) -> Dict[str, Any]:
        """Normalize all tool outputs to a canonical envelope."""
        if isinstance(result, dict):
            if "success" in result:
                normalized = {"success": bool(result.get("success"))}

                if "content" in result:
                    normalized["content"] = result.get("content")

                if "error" in result and result.get("error") is not None:
                    normalized["error"] = str(result.get("error"))
                elif not normalized["success"]:
                    exception_text = result.get("exception")
                    normalized["error"] = str(exception_text) if exception_text else f"Tool '{tool_name}' failed without an explicit error message."

                if normalized["success"] and "content" not in normalized and "message" in result:
                    normalized["content"] = result.get("message")

                return normalized

            if result.get("error") is not None or result.get("exception") is not None:
                return {
                    "success": False,
                    "error": str(result.get("error") or result.get("exception"))
                }

            if "content" in result:
                return {
                    "success": True,
                    "content": result.get("content")
                }

            return {
                "success": True,
                "content": result
            }

        return {
            "success": True,
            "content": result
        }

    def _tool_signature(self, name: str, args: Dict[str, Any]) -> str:
        """Create a stable signature for tool deduplication checks."""
        try:
            args_json = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        except Exception:
            args_json = str(args)
        return f"{name}::{args_json}"

    def _serialize_tool_result_for_model(self, tool_name: str, result: Dict[str, Any], reused: bool = False) -> str:
        """Serialize tool output for model context while keeping payload bounded and explicit."""
        payload = {
            "tool": tool_name,
            "success": bool(result.get("success", False)),
            "reused_cached_result": reused,
        }

        if result.get("error"):
            payload["error"] = str(result.get("error"))

        if "content" in result:
            payload["content"] = result.get("content")

        serialized = json.dumps(payload, ensure_ascii=False)
        max_chars = 12000
        if len(serialized) <= max_chars:
            return serialized

        content_preview = str(result.get("content", ""))[:4000]
        compact_payload = {
            "tool": tool_name,
            "success": bool(result.get("success", False)),
            "reused_cached_result": reused,
            "error": str(result.get("error")) if result.get("error") else None,
            "content_preview": content_preview,
            "_truncated": True,
            "_note": "Tool output truncated for context efficiency. Use this result before recalling the same tool with identical arguments.",
        }
        return json.dumps(compact_payload, ensure_ascii=False)

    def _normalize_tool_paths(self, session: AgentSession, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize bare file paths against recent tool directory context."""
        if not isinstance(args, dict):
            return args

        file_tools = {"read_file", "edit_file", "create_file", "delete_file"}
        if tool_name not in file_tools:
            return args

        file_path = args.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return args

        path_str = file_path.strip()
        if os.path.isabs(path_str):
            return args

        # If model already provided a nested relative path, keep it.
        if any(sep in path_str for sep in ("/", "\\")):
            return args

        last_dir = session.metadata.get("last_tool_directory")
        if not isinstance(last_dir, str) or not last_dir:
            return args

        candidate = os.path.join(last_dir, path_str)

        # For create_file, candidate may not exist yet; still a sensible normalization.
        if tool_name == "create_file" or os.path.exists(candidate):
            normalized = args.copy()
            normalized["file_path"] = candidate
            if self.debug:
                print(f"[Agent] 🧭 Normalized tool path for {tool_name}: '{path_str}' -> '{candidate}'")
            return normalized

        return args

    def _update_tool_directory_context(self, session: AgentSession, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]):
        """Track most recent working directory from successful file/list tool calls."""
        if not isinstance(result, dict) or not bool(result.get("success")):
            return

        if tool_name == "list_files":
            directory = args.get("directory") if isinstance(args, dict) else None
            if isinstance(directory, str) and directory.strip() and directory.strip() != ".":
                session.metadata["last_tool_directory"] = os.path.normpath(directory.strip())
            return

        if tool_name in {"create_file", "read_file", "edit_file", "delete_file"} and isinstance(args, dict):
            file_path = args.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                return

            normalized = os.path.normpath(file_path.strip())
            # For file targets, keep parent directory as context.
            if os.path.isdir(normalized):
                session.metadata["last_tool_directory"] = normalized
            else:
                parent = os.path.dirname(normalized)
                if parent:
                    session.metadata["last_tool_directory"] = parent

    def _load_default_skills(self):
        """Load default skills from the logicore/skills/defaults directory."""
        import os
        defaults_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "defaults")
        if os.path.exists(defaults_dir):
            default_skills = SkillLoader.discover(defaults_dir)
            for skill in default_skills:
                self._register_skill(skill)
            if self.debug and default_skills:
                print(f"[Agent] 🧩 Loaded {len(default_skills)} default skill(s): {[s.name for s in default_skills]}")

    def load_skills(self, skills):
        """
        Load skills by name (from defaults/workspace) or from Skill objects.
        
        Args:
            skills: List of skill names (str) or Skill objects.
                    String names are resolved from defaults and workspace.
        """
        import os
        defaults_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "defaults")
        
        for item in skills:
            if isinstance(item, Skill):
                self._register_skill(item)
            elif isinstance(item, str):
                # Try to load by name from defaults directory first
                skill_path = os.path.join(defaults_dir, item)
                skill = SkillLoader.load(skill_path)
                
                # Try workspace skills if not found in defaults
                if not skill and self.workspace_root:
                    ws_skills = SkillLoader.discover_workspace_skills(self.workspace_root)
                    for ws_skill in ws_skills:
                        if ws_skill.name.lower() == item.lower():
                            skill = ws_skill
                            break
                
                if skill:
                    self._register_skill(skill)
                else:
                    if self.debug:
                        print(f"[Agent] ⚠️ Skill not found: '{item}'")
            else:
                if self.debug:
                    print(f"[Agent] ⚠️ Invalid skill type: {type(item)}")
        
        # Rebuild prompt with new skills
        if self.skills:
            self._rebuild_system_prompt_with_tools()

    def load_skill(self, skill: Skill):
        """Load a single skill into the agent."""
        self._register_skill(skill)
        self._rebuild_system_prompt_with_tools()

    def _register_skill(self, skill: Skill):
        """Register a skill: add its tools and instructions."""
        # Avoid duplicates
        if any(s.name == skill.name for s in self.skills):
            if self.debug:
                print(f"[Agent] 🧩 Skill '{skill.name}' already loaded, skipping.")
            return
        
        self.skills.append(skill)
        
        # Register skill tools
        for tool_schema in skill.tools:
            self.internal_tools.append(tool_schema)
            tool_name = tool_schema.get("function", {}).get("name")
            if tool_name and tool_name in skill.tool_executors:
                self.skill_tool_executors[tool_name] = skill.tool_executors[tool_name]
        
        if skill.tools:
            self.supports_tools = True
        
        if self.debug:
            print(f"[Agent] 🧩 Loaded skill: '{skill.name}' ({len(skill.tools)} tools)")

    # --- Tool Management ---

    def load_default_tools(self):
        """Load all built-in tools (Filesystem, Web, Execution)."""
        self.internal_tools.extend(ALL_TOOL_SCHEMAS)
        self.supports_tools = True
        # Auto-load default skills from package defaults
        self._load_default_skills()
        self._rebuild_system_prompt_with_tools()  # Update system prompt with tools
        if self.debug:
            print(f"[Agent] Loaded {len(ALL_TOOL_SCHEMAS)} default tools.")
    
    def set_system_message(self, system_message: str):
        """
        Update the system message (preserves existing tools).
        Useful for changing behavior instructions while keeping loaded tools.
        """
        self._custom_system_message = system_message
        self._rebuild_system_prompt_with_tools()
        if self.debug:
            print(f"[Agent] System message updated")

    async def set_project_context(self, project_id: str, session_id: str = "default"):
        """
        Load a ProjectMemory context and inject it as a system message for the session.
        Injects the project's stored approaches, patterns, decisions, and preferences.
        Safe to call multiple times — replaces previous project context if already set.
        """
        from logicore.memory.project_memory import ProjectMemory
        pm = ProjectMemory()
        context_md = pm.export_project_context(project_id)
        if not context_md:
            if self.debug:
                print(f"[Agent] ⚠️ No project context found for project_id='{project_id}'")
            return

        session = self.get_session(session_id)
        project_msg = {
            "role": "system",
            "content": f"<project_context>\n{context_md}\n</project_context>"
        }

        # Replace existing project context if already present
        for i, msg in enumerate(session.messages):
            if msg.get("role") == "system" and "<project_context>" in msg.get("content", ""):
                session.messages[i] = project_msg
                if self.debug:
                    print(f"[Agent] 🗂️ Replaced project context for '{project_id}'")
                return

        # Otherwise insert after the base system message
        insert_idx = 1 if session.messages and session.messages[0].get("role") == "system" else 0
        session.messages.insert(insert_idx, project_msg)
        if self.debug:
            print(f"[Agent] 🗂️ Loaded project context for '{project_id}' ({len(context_md)} chars)")

    def disable_tools(self, reason: str = None):
        """Disable tool support for this agent."""
        self.supports_tools = False
        self.internal_tools = []
        self.tools_disabled_reason = reason or "Tools disabled"
        if self.debug:
            print(f"[Agent] Tools disabled: {self.tools_disabled_reason}")

    async def clear_mcp_servers(self):
        """Disconnect and remove all MCP servers."""
        for manager in self.mcp_managers:
            await manager.cleanup()
        self.mcp_managers = []
        if self.debug:
            print("[Agent] Cleared all MCP servers")

    async def add_mcp_server(self, config_path: str = "mcp.json", config: Dict[str, Any] = None):
        """Connect to MCP servers defined in a config file and add their tools."""
        manager = MCPClientManager(config_path, config=config)
        await manager.connect_to_servers()
        self.mcp_managers.append(manager)
        if self.debug:
            source = "memory" if config else config_path
            print(f"[Agent] Added MCP servers from {source}")

    def add_custom_tool(self, schema: Dict[str, Any], executor: Callable):
        """Add a single custom tool with its schema and execution function."""
        self.internal_tools.append(schema)
        tool_name = schema.get("function", {}).get("name")
        if tool_name:
            self.custom_tool_executors[tool_name] = executor
            self.supports_tools = True  # Mark tools as supported when first tool is added
            self._rebuild_system_prompt_with_tools()  # Update system prompt with tools
            if self.debug:
                print(f"[Agent] Added custom tool: {tool_name}")

    def register_tool_from_function(self, func: Callable):
        """
        Automatically registers a Python function as a tool.
        Generates the schema from the function's signature and docstring.
        """
        import inspect
        import re
        
        name = func.__name__
        raw_doc = func.__doc__ or "No description provided."
        
        # Parse docstring: extract main description and per-param descriptions
        # Supports Google-style (Args:) and Sphinx-style (:param name:) docstrings
        doc_lines = raw_doc.strip().split('\n')
        description_lines = []
        param_docs = {}
        
        in_args_section = False
        for line in doc_lines:
            stripped = line.strip()
            
            # Sphinx style: :param name: description
            sphinx_match = re.match(r':param\s+(\w+)\s*:(.*)', stripped)
            if sphinx_match:
                param_docs[sphinx_match.group(1)] = sphinx_match.group(2).strip()
                continue
            
            # Google style: "Args:" header
            if stripped.lower() in ('args:', 'arguments:', 'parameters:', 'params:'):
                in_args_section = True
                continue
            
            # Google style: "Returns:", "Raises:", etc. ends the Args section
            if stripped.lower().rstrip(':') in ('returns', 'raises', 'yields', 'examples', 'note', 'notes'):
                in_args_section = False
                continue
            
            if in_args_section and stripped:
                # Google style: "param_name (type): description" or "param_name: description"
                arg_match = re.match(r'(\w+)\s*(?:\([^)]*\))?\s*:(.*)', stripped)
                if arg_match:
                    param_docs[arg_match.group(1)] = arg_match.group(2).strip()
                continue
            
            if not in_args_section and stripped:
                description_lines.append(stripped)
        
        description = ' '.join(description_lines) if description_lines else raw_doc.strip()
        
        sig = inspect.signature(func)
        type_hints = get_type_hints(func)
        
        parameters = {
            "type": "object",
            "properties": {},
            "required": []
        }
        
        for param_name, param in sig.parameters.items():
            if param_name == 'self': continue
            
            # Map Python types to JSON types
            py_type = type_hints.get(param_name, str)
            json_type = "string"
            if py_type == int: json_type = "integer"
            elif py_type == float: json_type = "number"
            elif py_type == bool: json_type = "boolean"
            elif py_type == list: json_type = "array"
            elif py_type == dict: json_type = "object"
            
            # Use parsed docstring description, fallback to readable default
            pdesc = param_docs.get(param_name, f"The {param_name.replace('_', ' ')} value")
            
            parameters["properties"][param_name] = {
                "type": json_type,
                "description": pdesc
            }
            
            if param.default == inspect.Parameter.empty:
                parameters["required"].append(param_name)
                
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description.strip(),
                "parameters": parameters
            }
        }
        
        self.add_custom_tool(schema, func)

    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """Aggregate all tools (Internal + MCP), filtering out disabled ones."""
        filtered_tools = []
        
        # Process Internal Tools
        for tool in self.internal_tools:
            name = tool.get("function", {}).get("name")
            # We check both the name and a 'builtin:name' prefix for clarity
            if name and name not in self.disabled_tools and f"builtin:{name}" not in self.disabled_tools:
                filtered_tools.append(tool)
        
        # Process MCP Tools
        for manager in self.mcp_managers:
            mcp_tools = await manager.get_tools()
            for tool in mcp_tools:
                name = tool.get("function", {}).get("name")
                # Find which server this tool belongs to (manager should know)
                server_name = "unknown"
                if hasattr(manager, 'server_tools_map'):
                    server_name = manager.server_tools_map.get(name, "unknown")
                
                # Check server-level disabling and tool-level disabling
                if server_name not in self.disabled_tools and \
                   f"mcp_server:{server_name}" not in self.disabled_tools:
                    
                    tool_id = f"mcp:{server_name}:{name}"
                    if tool_id not in self.disabled_tools and name not in self.disabled_tools:
                        filtered_tools.append(tool)
            
        return filtered_tools

    # --- Session Management ---

    def get_session(self, session_id: str = "default") -> AgentSession:
        """Get or create a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = AgentSession(session_id, self.default_system_message)
        return self.sessions[session_id]

    def clear_session(self, session_id: str = "default"):
        if session_id in self.sessions:
            self.sessions[session_id].clear_history()

    # --- Execution ---

    def set_callbacks(self, **kwargs):
        """Set callbacks: on_tool_start, on_tool_end, on_tool_approval, on_final_message, on_token."""
        self.callbacks.update(kwargs)

    def _is_reminder_like_request(self, text: Any) -> bool:
        request = str(text or "").lower()
        return bool(
            re.search(
                r"\b(remind|reminder|notify|notification|ping me|in next \d+\s*(sec|second|seconds|min|minute|minutes))\b",
                request,
            )
        )

    def _has_unverified_reminder_claim(self, content: str) -> bool:
        response = (content or "").lower()
        claim_patterns = [
            r"\b(i('| wi)?ll|i can|got it)\b.*\b(remind|reminder|ping|notify)\b",
            r"\b(pop|ping)\b.*\b(in\s+\d+\s*(sec|second|seconds|min|minute|minutes))\b",
            r"\bi('| wi)?ll\s+.*\b(in\s+\d+\s*(sec|second|seconds|min|minute|minutes))\b",
        ]
        return any(re.search(pattern, response) for pattern in claim_patterns)

    def _extract_reminder_window_seconds(self, text: Any) -> Optional[int]:
        request = str(text or "").lower()

        m = re.search(r"(\d+)\s*(sec|second|seconds|min|minute|minutes|hr|hour|hours)", request)
        if not m:
            return None

        value = int(m.group(1))
        unit = m.group(2)

        if unit.startswith("sec"):
            return value
        if unit.startswith("min"):
            return value * 60
        if unit.startswith("hr") or unit.startswith("hour"):
            return value * 3600
        return None

    def _build_reminder_routing_hint(self, text: Any, tool_names: List[str]) -> Optional[str]:
        if not self._is_reminder_like_request(text):
            return None

        seconds = self._extract_reminder_window_seconds(text)
        has_cron = "add_cron_job" in tool_names

        if seconds is not None and seconds < 60:
            return (
                "<reminder_routing_hint>\n"
                "User requested a sub-minute reminder. Cron tools are minute-granularity and cannot satisfy seconds-level reminders. "
                "Do not call add_cron_job for this request. Explain limitation and ask for either rounding to the next minute or explicit approval for a one-shot execution tool.\n"
                "</reminder_routing_hint>"
            )

        if has_cron:
            return (
                "<reminder_routing_hint>\n"
                "For reminder/scheduling requests that are minute-level or greater, prefer cron tools first: add_cron_job (and list_cron_jobs to confirm). "
                "Avoid execute_command/code_execute for scheduling when cron can handle it.\n"
                "</reminder_routing_hint>"
            )

        return None

    def _extract_references_from_text(self, text: str) -> List[str]:
        """Extract URL/local path-like references from free-form text."""
        if not isinstance(text, str) or not text.strip():
            return []

        image_or_doc_ext = r"(?:png|jpg|jpeg|webp|bmp|gif|tif|tiff|pdf|ppt|pptx|doc|docx|xls|xlsx|csv|txt|md|py|js|ts|json|xml|html|css)"
        patterns = [
            r"https?://[^\s'\"<>]+",
            r"['\"]([A-Za-z]:\\[^'\"\r\n]+)['\"]",
            rf"([A-Za-z]:\\[^\r\n]*?\.{image_or_doc_ext})",
            r"([A-Za-z]:\\[^\s'\"\r\n]+)",
            r"['\"]((?:\.{1,2}[\\/])[^'\"\r\n]+)['\"]",
            rf"((?:\.{1,2}[\\/])[^\r\n]*?\.{image_or_doc_ext})",
            r"((?:\.{1,2}[\\/])[^\s'\"\r\n]+)",
            r"['\"]((?:/[^'\"\r\n]+)+)['\"]",
            rf"((?:/[^\r\n]+)+\.{image_or_doc_ext})",
            r"((?:/[^\s'\"\r\n]+)+)",
        ]

        refs: List[str] = []
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                ref = match.group(1) if match.groups() else match.group(0)
                ref = ref.strip().strip("'\"")
                ref = ref.rstrip(".,;:!?)]}")
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _resolve_local_reference(self, ref: str) -> Optional[str]:
        """Resolve local file references to absolute paths when possible."""
        if not isinstance(ref, str) or not ref:
            return None

        if ref.startswith(("http://", "https://")):
            return None

        candidates = []
        if os.path.isabs(ref):
            candidates.append(ref)
        else:
            candidates.append(os.path.abspath(ref))
            if self.workspace_root:
                candidates.append(os.path.abspath(os.path.join(self.workspace_root, ref)))

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    def _is_image_reference(self, ref: str) -> bool:
        lower = str(ref or "").lower()
        image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
        return lower.endswith(image_exts)

    def _extract_text_from_local_file(self, file_path: str) -> Optional[str]:
        """Extract text from a local file via document handlers."""
        try:
            from logicore.document_handlers.registry import get_handler
            handler = get_handler(file_path)
            text = handler.get_text()
            return text if isinstance(text, str) and text.strip() else None
        except Exception:
            return None

    def _extract_text_from_url(self, url: str) -> Optional[str]:
        """Fetch URL content and extract text if possible."""
        try:
            import httpx

            with httpx.Client(follow_redirects=True, timeout=20.0) as client:
                response = client.get(url)
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()

                if "text/" in content_type or "json" in content_type or "xml" in content_type:
                    return response.text

                parsed = urlparse(url)
                _, ext = os.path.splitext(parsed.path)
                if not ext:
                    return None

                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(response.content)
                    temp_path = tmp.name

                try:
                    return self._extract_text_from_local_file(temp_path)
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
        except Exception:
            return None

    def _auto_enrich_user_input(self, user_input: Union[str, List[Dict[str, Any]]]) -> Union[str, List[Dict[str, Any]]]:
        """Generic input enrichment for string prompts: detect refs, attach images, and inject extracted text context."""
        if not isinstance(user_input, str):
            return user_input

        refs = self._extract_references_from_text(user_input)
        if not refs:
            return user_input

        image_refs: List[str] = []
        context_chunks: List[str] = []
        cleaned_text = user_input

        max_sources = 4
        max_chars_per_source = 1800
        processed = 0

        for ref in refs:
            if processed >= max_sources:
                break

            is_url = ref.startswith(("http://", "https://"))
            local_path = self._resolve_local_reference(ref) if not is_url else None

            if self._is_image_reference(ref):
                if is_url:
                    image_refs.append(ref)
                    cleaned_text = cleaned_text.replace(ref, " ")
                    processed += 1
                    continue
                if local_path:
                    image_refs.append(local_path)
                    cleaned_text = cleaned_text.replace(ref, " ")
                    processed += 1
                    continue

            extracted = None
            source_label = ref
            if local_path:
                extracted = self._extract_text_from_local_file(local_path)
                source_label = local_path
            elif is_url:
                extracted = self._extract_text_from_url(ref)

            if extracted:
                snippet = extracted.strip()
                if len(snippet) > max_chars_per_source:
                    snippet = snippet[:max_chars_per_source] + "\n...[truncated]"
                context_chunks.append(f"Source: {source_label}\n{snippet}")
                cleaned_text = cleaned_text.replace(ref, " ")
                processed += 1

        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        if not cleaned_text:
            cleaned_text = "Please analyze the attached references and answer the user request."

        if context_chunks:
            cleaned_text += "\n\n<auto_reference_context>\n" + "\n\n---\n\n".join(context_chunks) + "\n</auto_reference_context>"

        if image_refs:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": cleaned_text}]
            for image_ref in image_refs:
                parts.append({"type": "image_url", "image_url": {"url": image_ref}})
            if self.debug:
                print(f"[Agent] 🧠 Auto-enriched input: {len(image_refs)} image(s), {len(context_chunks)} text source(s)")
            return parts

        if context_chunks and self.debug:
            print(f"[Agent] 🧠 Auto-enriched input with {len(context_chunks)} referenced text source(s)")

        return cleaned_text

    async def chat(
        self, 
        user_input: Union[str, List[Dict[str, Any]]], 
        session_id: str = "default", 
        callbacks: Dict[str, Callable] = None, 
        stream: bool = False, 
        streaming_funct: Callable = None,
        generate_walkthrough: bool = False,
        **kwargs
    ) -> str:
        """Main chat loop."""
        from logicore.providers.utils import extract_content
        
        # Initialize execution tracking for this chat
        self.execution_log = []
        user_req_str = user_input if isinstance(user_input, str) else str(user_input)[:200]
        self.execution_log.append(f"Agent Started Task. User Request: {user_req_str}")
        
        # Merge ephemeral callbacks
        active_callbacks = self.callbacks.copy()
        if callbacks:
            active_callbacks.update(callbacks)
        
        # Override with explicit streaming function if provided
        if streaming_funct:
            active_callbacks["on_token"] = streaming_funct
            stream = True

        session = self.get_session(session_id)

        # Sync memory session ID so per-session isolation works correctly
        if self.memory_enabled and self.simplemem:
            self.simplemem.session_id = session_id

        # --- Handle Multimodal Input ---
        text_for_memory = user_input
        if isinstance(user_input, list):
            text_for_memory, _ = extract_content(user_input)



        # Auto-enrich plain-text prompts by resolving referenced files/URLs.
        # Keeps explicit multimodal inputs unchanged.
        user_input = self._auto_enrich_user_input(user_input)

        text_for_memory = user_input
        if isinstance(user_input, list):
            text_for_memory, _ = extract_content(user_input)



        start_time = time.time()

        session.add_message({"role": "user", "content": user_input})

        # --- Memory System (Revised) ---
        # REMOVED: Automatic memory context injection at chat start.
        # WHY: Auto-injection polluted context with stale data from casual conversations,
        # causing hallucinations and irrelevant information.
        # NEW APPROACH: Memory is now explicit and on-demand via RAG-based memory tool:
        # - Agents call the 'memory' tool (search/store/list) when they need context
        # - This prevents casual "hello" or "remind me" from polluting context
        # - Only significant learnings are auto-captured (not casual chat)
        # - Memory context is injected only when agent explicitly requests it via tool
        #
        if self.memory_enabled and self.simplemem:
            # Queue the message for SimpleMem indexing (for future searches)
            # but DO NOT inject its context into current session automatically
            try:
                _ = await self.simplemem.on_user_message(text_for_memory)
            except Exception as e:
                if self.debug:
                    print(f"[Agent] ⚠️ SimpleMem queueing failed: {e}")


        
        # Only get tools if they're supported
        all_tools = None
        tool_names: List[str] = []
        successful_tools_this_chat = 0
        if self.supports_tools:
            all_tools = await self.get_all_tools()
            tool_names = [
                t.get("function", {}).get("name", "")
                for t in all_tools
                if isinstance(t, dict)
            ]
            if self.debug and all_tools:
                print(f"[Agent] 🛠️ Loaded {len(all_tools)} available tools")
        else:
            if self.debug:
                print(f"[Agent] ℹ️ Tool-free mode: {self.tools_disabled_reason or 'Model does not support tools'}")

        reminder_hint = self._build_reminder_routing_hint(text_for_memory, tool_names)
        reminder_hint_added = False
        if reminder_hint:
            session.messages.insert(-1, {
                "role": "system",
                "content": reminder_hint
            })
            reminder_hint_added = True

        tool_result_cache: Dict[str, Dict[str, Any]] = {}

        for i in range(self.max_iterations):
            if self.debug:
                print(f"\n[Agent] 🔄 ITERATION {i+1}/{self.max_iterations}")
            
            # Track iteration in execution summary
            self.execution_log.append(f"--- Iteration {i+1} ---")
            
            # Track LLM request duration for telemetry
            llm_start_time = time.time()
            
            # 1. Get response from LLM
            response = None
            try:
                # Prepare messages for provider: strip images if vision not supported
                llm_messages = session.messages

                # Apply context compression if enabled (summarizes old messages when context grows long)
                if self.context_compression and self.context_middleware:
                    llm_messages = await self.context_middleware.manage_context(llm_messages)



                # Use streaming if on_token callback is set and provider supports it
                on_token = active_callbacks.get("on_token")
                has_stream = hasattr(self.gateway, 'chat_stream')
                use_stream = bool(has_stream and (stream or on_token is not None))
                
                if self.debug:
                    print(f"[Agent] 🎯 Streaming: requested={stream}, on_token={on_token is not None}, support={has_stream}, active={use_stream}")
                
                if use_stream:
                    if self.debug:
                        model_name = getattr(self.provider, 'model_name', 'LLM')
                        print(f"[Agent] 📡 Streaming response from {model_name}...")
                    response = await self.gateway.chat_stream(llm_messages, tools=all_tools, on_token=on_token)
                else:
                    if self.debug:
                        model_name = getattr(self.provider, 'model_name', 'LLM')
                        has_tools = " (with tools)" if all_tools else ""
                        print(f"[Agent] 🤖 Generating response from {model_name}{has_tools}...")
                    response = await self.gateway.chat(llm_messages, tools=all_tools)
            except Exception as e:
                # Error handling & Retry logic
                error_str = str(e).lower()
                
                # Broaden the check for empty/invalid response errors and now Internal Server Errors
                if (
                    "empty" in error_str 
                    or "tool calls" in error_str 
                    or "model output must contain" in error_str
                    or "output text or tool calls" in error_str
                    or "unexpected" in error_str
                    or "does not support tools" in error_str
                    or "internal server error" in error_str
                    or "status code: -1" in error_str
                    or "status code: 500" in error_str
                ):
                    if self.debug or "internal server error" in error_str: 
                        print(f"[Agent] ⚠️ Provider error: {error_str[:80]}... Retrying...")
                    
                    # Retry loop with tools
                    retry_success = False
                    try:
                        await asyncio.sleep(1) # Short delay
                        response = await self.gateway.chat(session.messages, tools=all_tools)
                        retry_success = True
                    except Exception as retry_error:
                        retry_error_str = str(retry_error).lower()
                        if "does not support tools" in retry_error_str:
                            if self.debug: print(f"[Agent] ⚠️ Model doesn't support tools. Switching to no-tool mode.")
                            break # Stop retrying with tools immediately
                                            
                    if not retry_success:
                        # Fallback to no tools as a last resort
                        if self.debug: print(f"[Agent] 🔄 Falling back to inference without tools...")
                        try:
                            await asyncio.sleep(1)
                            response = await self.gateway.chat(session.messages, tools=None)
                        except Exception as fallback_error:
                            # Last resort: return friendly error message
                            error_msg = f"I encountered an error from the model: {str(fallback_error)}. Please try again."
                            if self.debug: 
                                print(f"[Agent] ❌ All retries exhausted: {fallback_error}")
                            # Finalize summary with error
                            self.execution_log.append(f"Failed: LLM error exhausted retries. {fallback_error}")
                            if generate_walkthrough:
                                walkthrough = await self._generate_walkthrough_summary(session_id, active_callbacks, stream)
                                if walkthrough:
                                    error_msg += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"
                            if active_callbacks["on_final_message"]:
                                active_callbacks["on_final_message"](session_id, error_msg)
                            return error_msg
                else:
                    # Different error
                    print(f"\n[Agent] ❌ Runtime Error: {e}")
                    # Don't crash, just break or continue?
                    # User asked to continue session chat.
                    # We will return the error as a message to the user so they know something happened.
                    # Finalize summary with error
                    error_msg = f"Error during execution: {str(e)}"
                    self.execution_log.append(f"Failed with runtime error: {e}")
                    if generate_walkthrough:
                        walkthrough = await self._generate_walkthrough_summary(session_id, active_callbacks, stream)
                        if walkthrough:
                            error_msg += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"
                    return error_msg
            
            # If we still don't have a response, skip this iteration
            if response is None:
                continue

            # 2. Parse Response (Gateway returns NormalizedMessage)
            try:
                from logicore.providers.gateway import NormalizedMessage
                
                # Response is now a NormalizedMessage from gateway
                if isinstance(response, NormalizedMessage):
                    content = response.content
                    tool_calls = response.tool_calls
                else:
                    # Fallback for any non-normalized responses
                    content = getattr(response, 'content', str(response))
                    tool_calls = getattr(response, 'tool_calls', [])
                
                if self.debug:
                    print(f"[Agent] Response parsed - Content length: {len(content) if content else 0}, Tool calls: {len(tool_calls) if tool_calls else 0}")
                    if content:
                        print(f"[Agent] Content preview: {content[:100]}...")
                    if tool_calls:
                        for idx, tc in enumerate(tool_calls):
                            tool_name = tc['function']['name'] if isinstance(tc, dict) else tc.function.name
                            print(f"[Agent]   Tool call {idx+1}: '{tool_name}'")
                
                # Convert to dict for session history
                msg_dict = {
                    "role": "assistant",
                    "content": content
                }
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                
                session.add_message(msg_dict)
                
                # Record telemetry if enabled
                if self.telemetry_enabled:
                    try:
                        from logicore.telemetry import TokenBreakdown
                        
                        llm_end_time = time.time()
                        duration_ms = (llm_end_time - llm_start_time) * 1000
                        
                        # Approximate token counts currently (1 token ~ 4 chars)
                        system_chars = sum(len(str(m.get("content", ""))) for m in session.messages if m.get("role") == "system")
                        message_chars = sum(len(str(m.get("content", ""))) for m in session.messages if m.get("role") != "system" and m.get("role") != "assistant")
                        tools_chars = len(json.dumps(all_tools)) if all_tools else 0
                        output_chars = len(str(content or ""))
                        
                        breakdown = TokenBreakdown(
                            system_instructions=system_chars // 4,
                            tool_definitions=tools_chars // 4,
                            messages=message_chars // 4
                        )
                        
                        input_tokens = (system_chars + message_chars + tools_chars) // 4
                        output_tokens = output_chars // 4
                        provider_name = getattr(self.llm, 'provider_name', 'unknown')
                        
                        self.telemetry_tracker.record_request(
                            session_id=session_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            model=self.model_name,
                            provider=provider_name,
                            duration_ms=duration_ms,
                            token_breakdown=breakdown,
                            tool_calls=len(tool_calls) if tool_calls else 0
                        )
                    except Exception as telemetry_err:
                        if self.debug:
                            print(f"[Agent] ⚠️ Telemetry tracking error: {telemetry_err}")
                    
            except Exception as parse_error:
                print(f"[Agent] ⚠️  Response Parsing Error (Ignored): {parse_error}")
                if self.debug:
                    import traceback
                    traceback.print_exc()
                # We can try to recover by adding a text message only
                try:
                     content_safe = getattr(response, 'content', str(response))
                     session.add_message({"role": "assistant", "content": f"(Recovered) {content_safe}"})
                except: pass
                continue

            # 3. Handle Final Response
            if not tool_calls:
                if reminder_hint_added:
                    for idx in range(len(session.messages) - 1, -1, -1):
                        msg = session.messages[idx]
                        if msg.get("role") == "system" and msg.get("content") == reminder_hint:
                            del session.messages[idx]
                            break

                if (
                    self._is_reminder_like_request(text_for_memory)
                    and successful_tools_this_chat == 0
                    and self._has_unverified_reminder_claim(content)
                ):
                    content = (
                        "I can’t trigger a real timed reminder inside this chat unless an approved tool runs successfully. "
                        "If you want, I can help set one up using an approved scheduler command or provide a local reminder script."
                    )

                # Store assistant response in memory and flush to vector store
                if self.memory_enabled and self.simplemem:
                    await self.simplemem.on_assistant_message(content)
                    await self.simplemem.process_pending()
                    if self.debug:
                        print(f"[Agent] 🧠 Memory stored for session '{session_id}'")

                if self.debug:
                    print(f"[Agent] ✅ No tool calls required. Returning response.")
                
                # Mark convergence in execution summary
                self.execution_log.append(f"Task successfully completed. Final LLM Response: {content[:200]}...")
                
                if generate_walkthrough:
                    if self.debug: print(f"[Agent] 📝 Generating walkthrough summary...")
                    walkthrough = await self._generate_walkthrough_summary(session_id, active_callbacks, stream)
                    if walkthrough:
                        content += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"

                if active_callbacks["on_final_message"]:
                    active_callbacks["on_final_message"](session_id, content)
                return content

            # 4. Execute Tools
            for tc in tool_calls:       

                # Extract details defensively (dict/object variants)
                try:
                    if isinstance(tc, dict):
                        func = tc.get('function')
                        if not isinstance(func, dict):
                            logger.warning(f"[Agent] ⚠️ Skipping malformed tool call without function block: {tc}")
                            continue
                        name = func.get('name')
                        args = func.get('arguments', {})
                        tc_id = tc.get('id')
                    else:
                        func = getattr(tc, 'function', None)
                        name = getattr(func, 'name', None) if func else None
                        args = getattr(func, 'arguments', {}) if func else {}
                        tc_id = getattr(tc, 'id', None)

                    if not name:
                        logger.warning(f"[Agent] ⚠️ Skipping malformed tool call missing name: {tc}")
                        continue
                except Exception as extract_err:
                    logger.error(f"[Agent] ⚠️ Failed to parse tool call payload: {extract_err}")
                    continue
                
                # Robustly ensure args is a mapping
                args, parse_error = self._parse_tool_arguments(name, args)
                args = self._normalize_tool_paths(session, name, args)
                signature = self._tool_signature(name, args)
                if parse_error:
                    result = self._normalize_tool_result(name, {"success": False, "error": parse_error})
                    tool_fail_log = f"[Agent] ❌ TOOL FAILED: '{name}' | Error: {parse_error[:120]}..."
                    print(tool_fail_log)
                    logger.error(tool_fail_log)
                    self.execution_log.append(f"Tool {name} FAILED with error: {parse_error}")

                    if active_callbacks["on_tool_end"]:
                        callback = active_callbacks["on_tool_end"]
                        if inspect.iscoroutinefunction(callback):
                            await callback(session_id, name, result)
                        else:
                            callback(session_id, name, result)

                    tool_msg = {
                        "role": "tool",
                        "name": name,
                        "content": self._serialize_tool_result_for_model(name, result, reused=False)
                    }
                    if tc_id:
                        tool_msg["tool_call_id"] = tc_id
                    session.add_message(tool_msg)
                    continue
                

                # Format tool parameters for logging (first 100 words)
                params_str = json.dumps(args) if isinstance(args, dict) else str(args)
                params_preview = (params_str[:150] + "...") if len(params_str) > 150 else params_str

                # Increment tool call telemetry if enabled
                if self.telemetry_enabled:
                    try:
                        # Ensure session exists (creates if needed)
                        if hasattr(self.telemetry_tracker, '_get_session'):
                            session_metrics = self.telemetry_tracker._get_session(session_id)
                            session_metrics.tool_calls += 1
                    except (AttributeError, Exception):
                        # Gracefully handle telemetry errors
                        pass
                    
                if active_callbacks["on_tool_start"]:
                    callback = active_callbacks["on_tool_start"]
                    if inspect.iscoroutinefunction(callback):
                        await callback(session_id, name, args)
                    else:
                        callback(session_id, name, args)

                # Clear logging: Show tool being called with params
                if self.debug:
                    tool_call_log = f"[Agent] 🔧 TOOL CALL: '{name}' | Params: {params_preview}"
                    print(tool_call_log)
                    logger.info(tool_call_log)
                else:
                    print(f"[Agent] 🔧 TOOL CALL: '{name}'")

                # Approval
                approved = True
                result = None
                if self._requires_approval(name):
                    if active_callbacks["on_tool_approval"]:
                        approval_result = await active_callbacks["on_tool_approval"](session_id, name, args)
                        
                        if isinstance(approval_result, dict):
                            # User modified arguments
                            args = approval_result
                            approved = True
                        else:
                            # Boolean or None
                            approved = bool(approval_result)
                    elif self._can_prompt_interactive_approval():
                        approval_result = await self._default_tool_approval_prompt(session_id, name, args)
                        if isinstance(approval_result, dict):
                            args = approval_result
                            approved = True
                        else:
                            approved = bool(approval_result)
                    else:
                        # Secure default: deny approval-required tools when no approval callback is configured.
                        approved = False
                        result = self._normalize_tool_result(name, {
                            "success": False,
                            "error": (
                                f"Tool '{name}' requires explicit approval, but no approval callback is configured. "
                                "Add on_tool_approval callback or use a safe tool."
                            )
                        })
                        if self.debug:
                            print(f"[Agent] 🔒 Approval required for '{name}' but no callback configured; denying execution.")

                if not approved:
                    if 'result' not in locals() or not isinstance(result, dict) or not result.get("error"):
                        result = self._normalize_tool_result(name, {"success": False, "error": "Denied by user"})
                    tool_fail_log = f"[Agent] ❌ EXECUTION DENIED: '{name}'"
                    print(tool_fail_log)
                    logger.warning(tool_fail_log)
                    # Track denied tool call in summary
                    self.execution_log.append(f"Tool {name} was denied. Reason: {result.get('error', 'Denied by user')}")
                else:
                    cached_result = tool_result_cache.get(signature)
                    reused_cached_result = bool(cached_result and cached_result.get("success"))

                    if reused_cached_result:
                        result = cached_result
                        if self.debug:
                            print(f"[Agent] ♻️ Reusing cached tool result for '{name}' with identical arguments")
                        self.execution_log.append(f"Tool {name} reused cached result for identical arguments")
                    else:
                        # Record tool call start time
                        start_time_tool = time.time()
                        result = self._normalize_tool_result(name, await self._execute_tool(name, args, session_id))
                        duration_ms = (time.time() - start_time_tool) * 1000

                        # Auto-heal common edit workflow error:
                        # edit_file requires read_file first in tool implementation.
                        if (
                            name == "edit_file"
                            and not bool(result.get("success", True))
                            and isinstance(args, dict)
                            and args.get("file_path")
                            and "file must be read before editing" in str(result.get("error", "")).lower()
                        ):
                            file_path = args.get("file_path")
                            if self.debug:
                                print(f"[Agent] 🛠️ Auto-recovery: running read_file before edit_file retry for '{file_path}'")

                            self.execution_log.append(
                                f"Auto-recovery triggered for edit_file on {file_path}: read-before-edit enforcement detected"
                            )

                            # Safe internal pre-read (no approval required for read_file)
                            _ = self._normalize_tool_result(
                                "read_file",
                                await self._execute_tool("read_file", {"file_path": file_path}, session_id),
                            )

                            # Retry edit once with original args
                            result = self._normalize_tool_result(name, await self._execute_tool(name, args, session_id))

                        tool_result_cache[signature] = result
                    
                    # Check if tool execution was successful
                    is_error = not bool(result.get("success", True))
                    
                    if is_error:
                        error_msg = result.get("error", "Unknown error")
                        tool_fail_log = f"[Agent] ❌ TOOL FAILED: '{name}' | Error: {str(error_msg)[:80]}..."
                        print(tool_fail_log)
                        logger.error(tool_fail_log)
                        # Track failed tool call in summary
                        self.execution_log.append(f"Tool {name} FAILED with error: {error_msg}")
                    else:
                        successful_tools_this_chat += 1
                        # Format result summary (up to 100 words)
                        result_str = json.dumps(result, ensure_ascii=False)
                        result_preview = (result_str[:120] + "...") if len(result_str) > 120 else result_str
                        tool_success_log = f"[Agent] ✅ TOOL SUCCESS: '{name}' | Result: {result_preview}"
                        print(tool_success_log)
                        if self.debug:
                            logger.info(tool_success_log)
                        # Track successful tool call in summary
                        self.execution_log.append(f"Tool {name} SUCCEEDED with result: {result_preview}")

                if active_callbacks["on_tool_end"]:
                    callback = active_callbacks["on_tool_end"]
                    if inspect.iscoroutinefunction(callback):
                        await callback(session_id, name, result)
                    else:
                        callback(session_id, name, result)

                self._update_tool_directory_context(session, name, args, result)

                # Add result to history - use better formatting for LLM clarity
                # Format: "Tool 'name' executed successfully. Result: {result_summary}"
                tool_msg = {
                    "role": "tool",
                    "name": name,
                    "content": self._serialize_tool_result_for_model(name, result, reused=reused_cached_result if approved else False)
                }
                if tc_id: 
                    tool_msg["tool_call_id"] = tc_id
                
                session.add_message(tool_msg)

        # Max iterations reached
        self.execution_log.append("Execution timed out: Max iterations reached.")
            
        final_msg = "Max iterations reached."
        if generate_walkthrough:
            walkthrough = await self._generate_walkthrough_summary(session_id, active_callbacks, stream)
            if walkthrough:
                final_msg += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"
                
        return final_msg

    async def _generate_walkthrough_summary(self, session_id: str, active_callbacks: dict, stream: bool = False) -> str:
        """Helper to generate the final walkthrough using the LLM itself."""
        if not self.execution_log:
            return ""
        
        execution_records = "\n".join(self.execution_log)
        walkthrough_prompt = (
            "Task execution is complete! Please review your execution details below and generate a 'Walkthrough Summary' for the user. "
            "Your summary must quickly explain what you successfully achieved, the status of any identified goals/tasks, "
            "and finally ask counter-questions or suggest clear next steps for the user.\n\n"
            f"Execution Records:\n{execution_records}"
        )
        
        session = self.get_session(session_id)
        session.add_message({"role": "user", "content": walkthrough_prompt})
        
        try:
            has_stream = hasattr(self.provider, 'chat_stream')
            on_token = active_callbacks.get("on_token") if active_callbacks else None
            
            if stream and on_token and has_stream:
                 # Stream the separator so the user knows the walkthrough is starting
                 if inspect.iscoroutinefunction(on_token):
                     await on_token("\n\n---\n### Walkthrough Summary\n")
                 else:
                     on_token("\n\n---\n### Walkthrough Summary\n")
                 response = await self.gateway.chat_stream(session.messages, tools=None, on_token=on_token)
            else:
                 response = await self.gateway.chat(session.messages, tools=None)
            
            from logicore.providers.gateway import NormalizedMessage
            if isinstance(response, NormalizedMessage):
                content = response.content
            else:
                content = getattr(response, 'content', str(response))
                
            session.add_message({"role": "assistant", "content": content})
            return content
        except Exception as e:
            if self.debug: print(f"[Agent] ⚠️ Failed generating walkthrough summary: {e}")
            return f"Walkthrough unavailable. Check logs. error={e}"

    def set_auto_approve_all(self, enabled: bool = True):
        """Enable/disable auto-approval for all tools (bypasses approval checks)."""
        self.auto_approve_all = enabled
        if self.debug:
            status = "enabled" if enabled else "disabled"
            print(f"[Agent] Auto-approval for all tools {status}")

    def _can_prompt_interactive_approval(self) -> bool:
        """Return True when interactive stdin is available for approval prompts."""
        try:
            return bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            return False

    async def _default_tool_approval_prompt(self, session_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Union[bool, Dict[str, Any]]:
        """Built-in fallback approval prompt for interactive CLI sessions.

        Options:
        - y / yes: approve this tool call
        - a / all: approve this call and auto-approve all subsequent tool calls
        - n / no / enter: deny this tool call
        """
        print("\n" + "=" * 60)
        print("TOOL APPROVAL REQUIRED")
        print("=" * 60)
        print(f"Session : {session_id}")
        print(f"Tool    : {tool_name}")
        try:
            print("Args    :", json.dumps(tool_args, indent=2, ensure_ascii=False))
        except Exception:
            print(f"Args    : {tool_args}")
        print("=" * 60)
        print("Allow this tool call? [y]es / [n]o / [a]ll: ", end="", flush=True)

        try:
            choice = input().strip().lower()
        except Exception:
            return False

        if choice in {"a", "all"}:
            self.set_auto_approve_all(True)
            print(f"[Approval] Approved '{tool_name}' and enabled auto-approve for all remaining tools")
            return True

        if choice in {"y", "yes"}:
            print(f"[Approval] Approved '{tool_name}'")
            return True

        print(f"[Approval] Denied '{tool_name}'")
        return False

    def _requires_approval(self, name: str) -> bool:
        """Check if a tool requires user approval."""
        # 0. If auto_approve_all is enabled, skip all approval checks
        if self.auto_approve_all:
            return False
        
        # 1. Allow Safe Tools Explicitly
        if name in SAFE_TOOLS:
            return False
            
        # Exempt 'computer' tool calls from approval (Claude Computer Use)
        if name == 'computer':
            return False

        # 2. Everything else requires approval
        # This covers DANGEROUS_TOOLS, APPROVAL_REQUIRED_TOOLS, and any unknown MCP/Custom tools
        return True



    async def _execute_tool(self, name: str, args: Dict, session_id: str) -> Any:
        # Logging Tool Execution
        logger.info(f"Tool Execution Start: {name} | Args: {args}")
        
        start_time = datetime.now()
        result = None
        
        try:
            # 1. Custom Tools
            if name in self.custom_tool_executors:
                executor = self.custom_tool_executors[name]
                if inspect.iscoroutinefunction(executor):
                    result = await executor(**args)
                else:
                    result = executor(**args)
            
            # 1b. Skill Tools
            elif name in self.skill_tool_executors:
                executor = self.skill_tool_executors[name]
                if inspect.iscoroutinefunction(executor):
                    result = await executor(**args)
                else:
                    result = executor(**args)
            
            # 2. MCP Tools
            elif any(name in manager.server_tools_map for manager in self.mcp_managers):
                 for manager in self.mcp_managers:
                    if name in manager.server_tools_map:
                        try:
                            result = await manager.execute_tool(name, args)
                            break
                        except Exception as e:
                            logger.error(f"Tool Error (MCP): {name} | Error: {e}")
                            return {"success": False, "error": str(e)}
                            
            # 3. Internal Default Tools
            else:
                result = execute_tool(name, args)

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Tool Execution End: {name} | Duration: {duration:.4f}s | Result: {str(result)[:200]}...") # Truncate result for logs
            return self._normalize_tool_result(name, result)

        except Exception as e:
            import traceback
            traceback.print_exc()
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"Tool Execution Failed: {name} | Duration: {duration:.4f}s | Error: {e}")
            return {"success": False, "error": f"Tool execution failed: {str(e)}"}

    def get_execution_summary(self) -> List[str]:
        """Get the current execution log for the last chat."""
        return self.execution_log
    
    def print_execution_summary(self) -> str:
        """Print a formatted text summary of the last execution."""
        if not self.execution_log:
            return "No execution logged - run chat() first"
        return "\n".join(self.execution_log)
    
    def get_execution_summary_dict(self) -> Optional[Dict[str, Any]]:
        """Get the execution summary as a dictionary."""
        if not self.execution_log:
            return None
        return {"log": self.execution_log}
    
    def get_execution_summary_json(self) -> Optional[str]:
        """Get the execution summary as JSON."""
        if not self.execution_log:
            return None
        return json.dumps({"log": self.execution_log}, indent=2)

    async def cleanup(self):
        """Clean up resources."""
        for manager in self.mcp_managers:
            await manager.cleanup()

        # Flush any unprocessed memories to vector store on teardown
        if self.memory_enabled and self.simplemem:
            try:
                await self.simplemem.process_pending()
            except Exception as e:
                if self.debug:
                    print(f"[Agent] ⚠️ Memory flush on cleanup failed: {e}")
