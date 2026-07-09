"""
Agent: Core agent class - refactored for composability.

Uses extracted components:
- ToolExecutor: Tool execution and management
- ChatOrchestrator: Chat loop and LLM interaction
- InputEnricher: Input enrichment and reference resolution
- ProviderFactory: Provider creation and management
"""

import os
import re
import json
import inspect
import asyncio
from typing import List, Dict, Any, Callable, Optional, Union, Tuple
from datetime import datetime
import logging

from logicore.providers.base import LLMProvider
from logicore.providers.factory import create_provider
from logicore.gateway.gateway import ProviderGateway, get_gateway_for_provider
from logicore.tools import ALL_TOOL_SCHEMAS, SAFE_TOOLS, TOOL_PRESETS
from logicore.config.prompts import get_system_prompt
from logicore.skills import Skill, SkillLoader
from logicore.telemetry.tracker import TelemetryTracker
from logicore.agent.session import AgentSession
from logicore.agent.tool_executor import ToolExecutor
from logicore.agent.chat_orchestrator import ChatOrchestrator
from logicore.agent.input_enricher import InputEnricher
from logicore.stream.emitter import StreamEmitter
from logicore.stream.result import AgentRunResult
from logicore.stream.events import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)


class Agent:
    """
    A unified, modular AI Agent that supports:
    - Internal tools (filesystem, web, etc.)
    - External MCP tools (Excel, etc.)
    - Multi-session management
    - Custom tool registration
    
    Uses composable components for extensibility.
    """
    
    def __init__(
        self,
        provider: Union[LLMProvider, str] = "ollama",
        model: str = None,
        api_key: str = None,
        endpoint: str = None,
        system_prompt: str = None,
        role: str = "general",
        debug: bool = False,
        tools: list = None,
        tool_preset: str = None,
        max_iterations: int = 40,
        telemetry: bool = False,
        skills: list = None,
        workspace_root: str = None,
        reasoning_level: str = "medium",
        plan_mode: bool = False,
        agent_id: str = None,
        # Composable components (optional overrides)
        tool_executor: Optional[ToolExecutor] = None,
        chat_orchestrator: Optional[ChatOrchestrator] = None,
        input_enricher: Optional[InputEnricher] = None,
    ):
        # Provider setup
        if isinstance(provider, str):
            self.provider = create_provider(provider, model, api_key, endpoint)
            self.model_name = model or "Default Model"
        else:
            self.provider = provider
            self.model_name = getattr(provider, "model_name", "Custom Provider")

        self.debug = debug

        # Make debug logging actually visible: the framework emits many
        # logger.debug(...) traces (gateway requests, tool calls, retries,
        # orchestration decisions) but Python's logging is unconfigured by
        # default, so debug=True appeared to do nothing.
        if debug:
            from logicore.logging_setup import setup_debug_logging
            setup_debug_logging()

        # Gateway
        self.gateway: ProviderGateway = get_gateway_for_provider(self.provider)
        
        try:
            setattr(self.provider, "debug", self.debug)
        except Exception as e:
            logger.debug(f"Could not set debug attribute on provider: {e}")

        # System prompt
        self.default_system_message = system_prompt or get_system_prompt(self.model_name, role)
        self._custom_system_message = system_prompt
        self.max_iterations = max_iterations
        self.role = role
        
        # Telemetry
        self.telemetry_enabled = telemetry
        self.telemetry_tracker = TelemetryTracker(enabled=telemetry)

        # Context engine
        from logicore.context_engine import ContextEngine
        from logicore.runtime.config import RuntimeConfig
        _rt_config = RuntimeConfig.from_settings()
        self.context_engine = ContextEngine(
            config=_rt_config,
            llm_provider=self.provider,
            model_name=getattr(self, 'model_name', 'default'),
            debug=debug,
            telemetry_tracker=self.telemetry_tracker if self.telemetry_enabled else None,
        )

        # Reasoning level
        self._reasoning_level = reasoning_level
        self._reasoning_controller = None
        try:
            from logicore.runtime.reasoning import ReasoningLevel, ReasoningConfig, ReasoningController
            level_map = {
                "minimal": ReasoningLevel.MINIMAL,
                "low": ReasoningLevel.LOW,
                "medium": ReasoningLevel.MEDIUM,
                "high": ReasoningLevel.HIGH,
                "deep": ReasoningLevel.DEEP,
            }
            config = ReasoningConfig(level=level_map.get(reasoning_level, ReasoningLevel.MEDIUM))
            self._reasoning_controller = ReasoningController(config)
        except ImportError:
            pass
        
        # Task Management
        from logicore.tasks import TaskManager, TaskStore, ActivityTracker, SessionProgressWriter, set_task_manager
        self._task_base_dir = workspace_root or os.getcwd()
        self._task_store = None
        self._task_manager = None
        self._task_tool_context = None  # Context for dependency injection
        self._activity_tracker = ActivityTracker()
        self._agent_id = agent_id or f"agent-{id(self)}"
        self._session_progress_writers: Dict[str, SessionProgressWriter] = {}
        
        # Plan Mode
        self._plan_mode_enabled = plan_mode
        self._planner = None
        if plan_mode:
            try:
                from logicore.runtime.planner import PlanService
                self._planner = PlanService(project_dir=workspace_root)
            except ImportError:
                pass

        # Tool Management
        self.internal_tools = []
        self.disabled_tools = set()
        self.workspace_root = workspace_root
        
        # Composable components
        self.tool_executor = tool_executor or ToolExecutor(debug=debug)
        self.input_enricher = input_enricher or InputEnricher(workspace_root=workspace_root, debug=debug)
        self._chat_orchestrator = chat_orchestrator or ChatOrchestrator(agent=self, debug=debug)
        
        # Skills Management
        self.skills: List[Skill] = []
        
        # Session Management
        self.sessions: Dict[str, AgentSession] = {}
        self._session_locks: Dict[str, "asyncio.Lock"] = {}
        
        # Execution Tracking
        self.execution_log: List[str] = []
        
        # Tool support flag
        self.supports_tools = False
        self.tools_disabled_reason = None
        
        # Handle tools parameter
        if tool_preset and tool_preset in TOOL_PRESETS:
            self.load_tools_preset(tool_preset)
        elif isinstance(tools, list) and len(tools) > 0:
            for tool in tools:
                if callable(tool):
                    self.register_tool_from_function(tool)
                elif isinstance(tool, dict):
                    self.internal_tools.append(tool)
                    tool_name = tool.get("function", {}).get("name")
                    if tool_name:
                        self.tool_executor.register_custom_tool(tool_name, tool)
            if len(self.internal_tools) > 0:
                self.supports_tools = True
                self._rebuild_system_prompt_with_tools()
        else:
            self.load_default_tools()
        
        if skills:
            self.load_skills(skills)
        
        # Callbacks
        self.callbacks = {
            "on_tool_start": None,
            "on_tool_end": None,
            "on_tool_approval": None,
            "on_final_message": None,
            "on_token": None
        }
        
        # Tool Approval Control
        self.auto_approve_all = False

    # === Properties ===
    
    @property
    def system_prompt(self) -> str:
        return self.default_system_message

    @property
    def telemetry(self) -> dict:
        if not self.telemetry_enabled:
            return {"error": "Telemetry is not enabled."}
        session_ids = self.telemetry_tracker.get_session_ids()
        if len(session_ids) == 1:
            return self.telemetry_tracker.get_session_summary(session_ids[0])
        elif len(session_ids) == 0:
            return {"message": "No telemetry data recorded yet."}
        return self.telemetry_tracker.get_total_summary()

    # === Reasoning Level API ===
    
    @property
    def reasoning_level(self) -> str:
        return self._reasoning_level
    
    @reasoning_level.setter
    def reasoning_level(self, level: str) -> None:
        valid_levels = ["minimal", "low", "medium", "high", "deep"]
        if level not in valid_levels:
            raise ValueError(f"Invalid reasoning level '{level}'. Must be one of: {valid_levels}")
        self._reasoning_level = level
        if self._reasoning_controller:
            from logicore.runtime.reasoning import ReasoningLevel
            level_map = {
                "minimal": ReasoningLevel.MINIMAL,
                "low": ReasoningLevel.LOW,
                "medium": ReasoningLevel.MEDIUM,
                "high": ReasoningLevel.HIGH,
                "deep": ReasoningLevel.DEEP,
            }
            self._reasoning_controller.set_level(level_map[level], reason="manual_api")
        if self.debug:
            logger.info(f"[Agent] Reasoning level set to: {level}")
    
    def set_reasoning_level(self, level: str) -> None:
        self.reasoning_level = level
    
    def get_reasoning_state(self) -> dict:
        if self._reasoning_controller:
            return self._reasoning_controller.get_state_summary()
        return {"level": self._reasoning_level, "controller": "not_initialized"}
    
    # === Plan Mode API ===
    
    @property
    def planner(self):
        return self._planner
    
    @property
    def is_in_plan_mode(self) -> bool:
        if self._planner:
            return self._planner.is_in_plan_mode
        return False

    # === System Prompt Management ===
    
    def _rebuild_system_prompt_with_tools(self):
        from logicore.config.prompts import _format_tools
        
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

    # === Skill Management ===
    
    def _build_skills_prompt_section(self) -> str:
        if not self.skills:
            return ""
        blocks = []
        for skill in self.skills:
            if not skill.is_enabled:
                continue
            block = f"### Skill: {skill.name}\n"
            block += f"_{skill.description}_\n\n"
            block += skill.instructions
            if skill.system_prompt_addon:
                block += f"\n\n{skill.system_prompt_addon}"
            blocks.append(block)
        if not blocks:
            return ""
        skills_str = "\n\n---\n\n".join(blocks)
        return f"\n\n## Active Skills\n{skills_str}"

    def _load_default_skills(self):
        defaults_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "defaults")
        if os.path.exists(defaults_dir):
            index_entries, direct_skills = SkillLoader.discover_with_index(defaults_dir)
            for skill in direct_skills:
                self._register_skill(skill)
            if not hasattr(self, '_skill_index_entries'):
                self._skill_index_entries = {}
            for entry in index_entries:
                self._skill_index_entries[entry.name] = (defaults_dir, entry)

    def _load_default_skills_for_preset(self, preset: str):
        skip_skill_presets = {"minimal", "lightweight"}
        if preset in skip_skill_presets:
            return
        self._load_default_skills()
        
    def load_skills(self, skills):
        defaults_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "defaults")
        for item in skills:
            if isinstance(item, Skill):
                self._register_skill(item)
            elif isinstance(item, str):
                if any(s.name == item for s in self.skills):
                    continue
                skill_path = os.path.join(defaults_dir, item)
                skill = SkillLoader.load(skill_path)
                if not skill and hasattr(self, '_skill_index_entries') and item in self._skill_index_entries:
                    skill_dir, entry = self._skill_index_entries[item]
                    skill = SkillLoader.load_skill_by_index(skill_dir, item)
                if not skill and self.workspace_root:
                    ws_skills = SkillLoader.discover_workspace_skills(self.workspace_root)
                    for ws_skill in ws_skills:
                        if ws_skill.name.lower() == item.lower():
                            skill = ws_skill
                            break
                if skill:
                    self._register_skill(skill)
        if self.skills:
            self._rebuild_system_prompt_with_tools()

    def load_skill(self, skill: Skill):
        self._register_skill(skill)
        self._rebuild_system_prompt_with_tools()

    def unload_skill(self, skill_name: str) -> bool:
        skill_to_remove = None
        for skill in self.skills:
            if skill.name == skill_name:
                skill_to_remove = skill
                break
        if not skill_to_remove:
            return False
        skill_tool_names = set(skill_to_remove.get_tool_names())
        self.internal_tools = [
            t for t in self.internal_tools
            if t.get("function", {}).get("name") not in skill_tool_names
        ]
        for tool_name in skill_tool_names:
            self.tool_executor.unregister_skill_tool(tool_name)
        self.skills.remove(skill_to_remove)
        self._rebuild_system_prompt_with_tools()
        return True

    def enable_skill(self, skill_name: str) -> bool:
        for skill in self.skills:
            if skill.name == skill_name:
                skill.enable()
                self._rebuild_system_prompt_with_tools()
                return True
        return False

    def disable_skill(self, skill_name: str) -> bool:
        for skill in self.skills:
            if skill.name == skill_name:
                skill.disable()
                self._rebuild_system_prompt_with_tools()
                return True
        return False

    def load_skill_from_index(self, skill_name: str) -> bool:
        if not hasattr(self, '_skill_index_entries') or skill_name not in self._skill_index_entries:
            return False
        skills_dir, entry = self._skill_index_entries[skill_name]
        skill = SkillLoader.load_skill_by_index(skills_dir, skill_name)
        if skill:
            self._register_skill(skill)
            self._rebuild_system_prompt_with_tools()
            return True
        return False

    def list_available_skills(self) -> List[Dict[str, Any]]:
        result = []
        for skill in self.skills:
            result.append({
                "name": skill.name,
                "description": skill.description,
                "status": "loaded",
                "tools": len(skill.tools),
                "enabled": skill.is_enabled
            })
        if hasattr(self, '_skill_index_entries'):
            loaded_names = {s.name for s in self.skills}
            for name, (skills_dir, entry) in self._skill_index_entries.items():
                if name not in loaded_names:
                    result.append({
                        "name": entry.name,
                        "description": entry.description,
                        "status": "indexed",
                        "trigger": entry.trigger,
                        "cost_tier": entry.cost_tier
                    })
        return result

    def _register_skill(self, skill: Skill):
        if any(s.name == skill.name for s in self.skills):
            return
        self.skills.append(skill)
        for tool_schema in skill.tools:
            tool_name = tool_schema.get("function", {}).get("name")
            existing_names = {
                t.get("function", {}).get("name") for t in self.internal_tools
                if isinstance(t, dict)
            }
            if tool_name in existing_names:
                logger.warning(f"Tool naming conflict: skill '{skill.name}' registers '{tool_name}'")
            self.internal_tools.append(tool_schema)
            if tool_name and tool_name in skill.tool_executors:
                self.tool_executor.register_skill_tool(tool_name, skill.tool_executors[tool_name])
        if skill.tools:
            self.supports_tools = True

    # === Tool Management ===
    
    def load_default_tools(self):
        from logicore.tasks import get_task_tools, get_task_tool_schemas, get_task_tools_with_context
        # Ensure task manager is initialized
        self._ensure_task_manager()
        # Use context-based tools if available (multi-agent safe)
        tools = get_task_tools_with_context(self._task_tool_context) if self._task_tool_context else get_task_tools()
        self.internal_tools.extend(get_task_tool_schemas())
        for tool in tools:
            self.tool_executor.register_custom_tool(tool.name, tool.run)
        self.internal_tools.extend(ALL_TOOL_SCHEMAS)
        self.supports_tools = True
        self._load_default_skills()
        self._rebuild_system_prompt_with_tools()
    
    def load_tools_preset(self, preset: str):
        if preset == "full":
            self.load_default_tools()
            return
        if preset not in TOOL_PRESETS:
            return
        preset_tools = TOOL_PRESETS[preset]
        from logicore.tasks import get_task_tools, get_task_tool_schemas, get_task_tools_with_context
        # Ensure task manager is initialized
        self._ensure_task_manager()
        # Use context-based tools if available (multi-agent safe)
        tools = get_task_tools_with_context(self._task_tool_context) if self._task_tool_context else get_task_tools()
        self.internal_tools.extend(get_task_tool_schemas())
        for tool in tools:
            self.tool_executor.register_custom_tool(tool.name, tool.run)
        from logicore.tools.registry import ToolRegistry
        temp_registry = ToolRegistry(enabled_tools=preset_tools)
        self.internal_tools.extend(temp_registry.schemas)
        self.supports_tools = True
        self._load_default_skills_for_preset(preset)
        self._rebuild_system_prompt_with_tools()
    
    def set_system_prompt(self, system_prompt: str):
        self._custom_system_message = system_prompt
        self._rebuild_system_prompt_with_tools()

    def disable_tools(self, reason: str = None):
        self.supports_tools = False
        self.internal_tools = []
        self.tools_disabled_reason = reason or "Tools disabled"

    async def clear_mcp_servers(self):
        for manager in self.tool_executor.mcp_managers:
            await manager.cleanup()
        self.tool_executor.mcp_managers = []

    async def add_mcp_server(self, config_path: str = "mcp.json", config: Dict[str, Any] = None):
        from logicore.mcp.client import MCPClientManager
        manager = MCPClientManager(config_path, config=config)
        await manager.connect_to_servers()
        self.tool_executor.add_mcp_manager(manager)

    def add_custom_tool(self, schema: Dict[str, Any], executor: Callable):
        self.internal_tools.append(schema)
        tool_name = schema.get("function", {}).get("name")
        if tool_name:
            self.tool_executor.register_custom_tool(tool_name, executor)
            self.supports_tools = True
            self._rebuild_system_prompt_with_tools()

    def register_tool_from_function(self, func: Callable):
        import re
        name = func.__name__
        raw_doc = func.__doc__ or "No description provided."
        
        doc_lines = raw_doc.strip().split('\n')
        description_lines = []
        param_docs = {}
        
        in_args_section = False
        for line in doc_lines:
            stripped = line.strip()
            sphinx_match = re.match(r':param\s+(\w+)\s*:(.*)', stripped)
            if sphinx_match:
                param_docs[sphinx_match.group(1)] = sphinx_match.group(2).strip()
                continue
            if stripped.lower() in ('args:', 'arguments:', 'parameters:', 'params:'):
                in_args_section = True
                continue
            if stripped.lower().rstrip(':') in ('returns', 'raises', 'yields', 'examples', 'note', 'notes'):
                in_args_section = False
                continue
            if in_args_section and stripped:
                arg_match = re.match(r'(\w+)\s*(?:\([^)]*\))?\s*:(.*)', stripped)
                if arg_match:
                    param_docs[arg_match.group(1)] = arg_match.group(2).strip()
                continue
            if not in_args_section and stripped:
                description_lines.append(stripped)
        
        description = ' '.join(description_lines) if description_lines else raw_doc.strip()
        
        sig = inspect.signature(func)
        from typing import get_type_hints
        type_hints = get_type_hints(func)
        
        parameters = {"type": "object", "properties": {}, "required": []}
        for param_name, param in sig.parameters.items():
            if param_name == 'self': continue
            py_type = type_hints.get(param_name, str)
            json_type = "string"
            if py_type == int: json_type = "integer"
            elif py_type == float: json_type = "number"
            elif py_type == bool: json_type = "boolean"
            elif py_type == list: json_type = "array"
            elif py_type == dict: json_type = "object"
            pdesc = param_docs.get(param_name, f"The {param_name.replace('_', ' ')} value")
            parameters["properties"][param_name] = {"type": json_type, "description": pdesc}
            if param.default == inspect.Parameter.empty:
                parameters["required"].append(param_name)
                
        schema = {
            "type": "function",
            "function": {"name": name, "description": description.strip(), "parameters": parameters}
        }
        self.add_custom_tool(schema, func)

    # === Session Management ===
    
    def _get_session_lock(self, session_id: str) -> "asyncio.Lock":
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    def get_session(self, session_id: str = "default") -> AgentSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = AgentSession(session_id, self.default_system_message)
        return self.sessions[session_id]

    def clear_session(self, session_id: str = "default"):
        if session_id in self.sessions:
            self.sessions[session_id].clear_history()
    
    def create_session(self, session_id: str = None, tags: Dict[str, str] = None) -> str:
        if session_id is None:
            import uuid
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        session = AgentSession(session_id, self.default_system_message)
        if tags:
            session.metadata["tags"] = tags
        session.metadata["created_at"] = datetime.now().isoformat()
        self.sessions[session_id] = session
        return session_id
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        result = []
        for session_id, session in self.sessions.items():
            result.append({
                "session_id": session_id,
                "tags": session.metadata.get("tags", {}),
                "message_count": len(session.messages),
                "created_at": session.created_at.isoformat() if hasattr(session, 'created_at') else None,
                "last_activity": session.last_activity.isoformat() if hasattr(session, 'last_activity') else None,
            })
        return result
    
    def delete_session(self, session_id: str) -> bool:
        if session_id not in self.sessions:
            return False
        del self.sessions[session_id]
        if self._task_store and self._task_store.task_list_id == session_id:
            self._task_manager = None
            self._task_store = None
        if session_id in self._session_progress_writers:
            del self._session_progress_writers[session_id]
        return True
    
    def get_session_by_tags(self, tags: Dict[str, str]) -> Optional[str]:
        for session_id, session in self.sessions.items():
            session_tags = session.metadata.get("tags", {})
            if all(session_tags.get(k) == v for k, v in tags.items()):
                return session_id
        return None

    # === Execution ===
    
    def set_callbacks(self, **kwargs):
        self.callbacks.update(kwargs)
        self.tool_executor.set_callbacks(**{k: v for k, v in kwargs.items() if k.startswith("on_tool")})

    def _is_reminder_like_request(self, text: Any) -> bool:
        request = str(text or "").lower()
        return bool(re.search(r"\b(remind|reminder|notify|notification|ping me|in next \d+\s*(sec|second|seconds|min|minute|minutes))\b", request))

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
        if unit.startswith("sec"): return value
        if unit.startswith("min"): return value * 60
        if unit.startswith("hr") or unit.startswith("hour"): return value * 3600
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
                "Do not call add_cron_job for this request.\n"
                "</reminder_routing_hint>"
            )
        if has_cron:
            return (
                "<reminder_routing_hint>\n"
                "For reminder/scheduling requests that are minute-level or greater, prefer cron tools first.\n"
                "</reminder_routing_hint>"
            )
        return None

    def _normalize_tool_paths(self, session: AgentSession, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
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
        if any(sep in path_str for sep in ("/", "\\")):
            return args
        last_dir = session.metadata.get("last_tool_directory")
        if not isinstance(last_dir, str) or not last_dir:
            return args
        candidate = os.path.join(last_dir, path_str)
        if tool_name == "create_file" or os.path.exists(candidate):
            normalized = args.copy()
            normalized["file_path"] = candidate
            return normalized
        return args

    def _update_tool_directory_context(self, session: AgentSession, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]):
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
            if os.path.isdir(normalized):
                session.metadata["last_tool_directory"] = normalized
            else:
                parent = os.path.dirname(normalized)
                if parent:
                    session.metadata["last_tool_directory"] = parent

    def _serialize_tool_result_for_model(self, tool_name: str, result: Dict[str, Any], reused: bool = False) -> str:
        return self.context_engine.distill_tool_result(tool_name, result, reused)

    # === Task Management ===
    
    def _ensure_task_manager(self, session_id: str = "default") -> None:
        current_task_list_id = self._task_store.task_list_id if self._task_store else None
        if self._task_manager is not None and current_task_list_id == session_id:
            return
        from logicore.tasks import TaskManager, TaskStore, SessionProgressWriter, set_task_manager, set_agent_id, TaskToolContext
        self._task_store = TaskStore(base_dir=self._task_base_dir, task_list_id=session_id)
        self._task_manager = TaskManager(self._task_store)
        # Create context for dependency injection (enables multi-agent scenarios)
        self._task_tool_context = TaskToolContext(task_manager=self._task_manager, agent_id=self._agent_id)
        # Keep backward compatibility with global state (deprecated for multi-agent)
        set_task_manager(self._task_manager, owner_id=self._agent_id)
        set_agent_id(self._agent_id, owner_id=self._agent_id)
        if session_id not in self._session_progress_writers:
            self._session_progress_writers[session_id] = SessionProgressWriter(
                workspace_root=self._task_base_dir,
                session_id=session_id,
            )

    # === Main Chat Method ===
    
    async def chat(
        self, 
        user_input: Union[str, List[Dict[str, Any]]], 
        session_id: str = "default", 
        callbacks: Dict[str, Callable] = None, 
        stream: bool = False, 
        streaming_funct: Callable = None,
        generate_walkthrough: bool = False,
        new_session: bool = False,
        session_tags: Dict[str, str] = None,
        **kwargs
    ) -> str:
        if new_session:
            session_id = self.create_session(tags=session_tags)
        elif session_tags and session_id not in self.sessions:
            session = self.get_session(session_id)
            session.metadata["tags"] = session_tags
        
        self._ensure_task_manager(session_id)
        self.execution_log = []
        self.execution_log.append(f"Agent Started Task. User Request: {str(user_input)[:200]}")
        
        # Enrich input
        user_input = await self.input_enricher.enrich_async(user_input)
        
        return await self._chat_orchestrator.run(
            user_input=user_input,
            session_id=session_id,
            callbacks=callbacks,
            stream=stream,
            streaming_funct=streaming_funct,
            generate_walkthrough=generate_walkthrough,
        )

    # === Streaming (event-based) API ===

    async def stream_run(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = "default",
        callbacks: Dict[str, Callable] = None,
        generate_walkthrough: bool = False,
        new_session: bool = False,
        session_tags: Dict[str, str] = None,
        **kwargs,
    ) -> "AgentRunResult":
        """
        Start a streaming agent run and return an :class:`AgentRunResult`.

        The returned object is both awaitable (``final = await run``) and an
        async iterator of :class:`StreamEvent` (``async for ev in run.stream_events()``).
        The agent loop runs as a background task, so a UI that raises or stops
        early only affects its own drain loop — call ``run.cancel()`` to stop
        the run early. This is the recommended API for frontend integration.

        Example:
            run = await agent.stream_run("summarize this repo", session_id="s1")
            async for ev in run.stream_events():
                await sse_send(ev)        # ev.to_sse() built-in
            final = await run              # final text
        """
        if new_session:
            session_id = self.create_session(tags=session_tags)
        elif session_tags and session_id not in self.sessions:
            session = self.get_session(session_id)
            session.metadata["tags"] = session_tags

        self._ensure_task_manager(session_id)
        self.execution_log = []
        self.execution_log.append(f"Agent Started Task. User Request: {str(user_input)[:200]}")

        user_input = await self.input_enricher.enrich_async(user_input)

        active_callbacks = self.callbacks.copy()
        if callbacks:
            active_callbacks.update(callbacks)

        import uuid
        emitter = StreamEmitter(session_id=session_id, run_id=uuid.uuid4().hex)

        async def _produce() -> None:
            try:
                final = await self._chat_orchestrator.run(
                    user_input=user_input,
                    session_id=session_id,
                    callbacks=active_callbacks,
                    generate_walkthrough=generate_walkthrough,
                    emitter=emitter,
                )
                emitter.final = final
            except asyncio.CancelledError:
                raise
            except Exception as e:  # isolate unexpected producer errors
                try:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.ERROR, {"message": str(e), "recoverable": False}
                    ))
                except Exception:
                    pass
                emitter.final = f"Error during execution: {e}"
            finally:
                emitter.close()

        task = asyncio.ensure_future(_produce())
        return AgentRunResult(emitter, task, self)

    async def stream(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = "default",
        **kwargs,
    ):
        """
        Convenience async generator that yields :class:`StreamEvent` objects for
        a single agent run. Cancels the underlying run if the consumer stops
        iterating early.

        Example:
            async for ev in agent.stream("hello", session_id="s1"):
                print(ev.type, ev.data)
        """
        run = await self.stream_run(user_input, session_id=session_id, **kwargs)
        async for ev in run.stream_events():
            yield ev

    def cancel_run(self, run: "AgentRunResult") -> None:
        """Cancel an in-flight streaming run."""
        if run is not None:
            run.cancel()

    def stream_sync(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = "default",
        on_event: Callable = None,
        **kwargs,
    ) -> str:
        """
        Synchronous streaming — **no server and no async framework required**.

        Runs the agent run to completion and invokes ``on_event(event)`` for
        every :class:`StreamEvent` as it arrives (e.g. print tokens, update a
        local UI). This is the simplest way to get live token streaming in a
        plain script; a web server is only needed if you want to push the same
        events to a browser, and is entirely optional.

        Args:
            user_input: User message (str or multimodal list).
            session_id: Session identifier.
            on_event: Callable invoked with each ``StreamEvent`` (optional).
            **kwargs: forwarded to :meth:`stream_run`.

        Returns:
            The final agent response as a string.

        Example (no server):
            agent = Agent(provider="ollama", model="llama3.2:3b")
            agent.stream_sync(
                "summarize this repo",
                on_event=lambda ev: (
                    print(ev.data.get("delta", ""), end="", flush=True)
                    if ev.type == "token" else None
                ),
            )
        """
        async def _drive():
            run = await self.stream_run(user_input, session_id=session_id, **kwargs)
            async for ev in run.stream_events():
                if on_event:
                    on_event(ev)
            return await run

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Not inside a running loop — safe to drive one to completion.
            return asyncio.run(_drive())
        raise RuntimeError(
            "stream_sync() cannot be called from within a running event loop. "
            "Use `async for ev in agent.stream(...)` instead."
        )

    # === Walkthrough Generation ===
    
    async def _generate_walkthrough_summary(self, session_id: str, active_callbacks: dict, stream: bool = False) -> str:
        if not self.execution_log:
            return ""
        execution_records = "\n".join(self.execution_log)
        walkthrough_prompt = (
            "Task execution is complete! Please review your execution details below and generate a 'Walkthrough Summary'.\n\n"
            f"Execution Records:\n{execution_records}"
        )
        session = self.get_session(session_id)
        session.add_message({"role": "user", "content": walkthrough_prompt})
        try:
            _walk_ctx, _walk_msgs = await self.context_engine.prepare_messages(session.messages, session_id=session_id)
            response = await self.gateway.chat(_walk_msgs, tools=None)
            from logicore.gateway.gateway import NormalizedMessage
            if isinstance(response, NormalizedMessage):
                content = response.content
            else:
                content = getattr(response, 'content', str(response))
            session.add_message({"role": "assistant", "content": content})
            return content
        except Exception as e:
            return f"Walkthrough unavailable. error={e}"

    # === Approval Control ===
    
    def set_auto_approve_all(self, enabled: bool = True):
        self.auto_approve_all = enabled
        self.tool_executor.set_auto_approve(enabled)

    # === Tool Approval ===
    
    def _requires_approval(self, name: str) -> bool:
        return self.tool_executor.requires_approval(name)

    # === Execution Summary ===
    
    def get_execution_summary(self) -> List[str]:
        return self.execution_log
    
    def print_execution_summary(self) -> str:
        if not self.execution_log:
            return "No execution logged"
        return "\n".join(self.execution_log)
    
    def get_execution_summary_dict(self) -> Optional[Dict[str, Any]]:
        if not self.execution_log:
            return None
        return {"log": self.execution_log}
    
    def get_execution_summary_json(self) -> Optional[str]:
        if not self.execution_log:
            return None
        return json.dumps({"log": self.execution_log}, indent=2)

    def _generate_execution_summary(self) -> str:
        if not self.execution_log:
            return "No execution steps were recorded."
        summary_parts = ["## Execution Summary", "", f"I completed **{len(self.execution_log)} execution steps**.", ""]
        tool_calls = [log for log in self.execution_log if "SUCCEEDED" in log or "FAILED" in log]
        if tool_calls:
            summary_parts.append("### Tools Used")
            summary_parts.append("```")
            for entry in tool_calls[-20:]:
                summary_parts.append(entry)
            summary_parts.append("```")
        return "\n".join(summary_parts)

    # === Cleanup ===
    
    async def cleanup(self):
        for manager in self.tool_executor.mcp_managers:
            await manager.cleanup()
