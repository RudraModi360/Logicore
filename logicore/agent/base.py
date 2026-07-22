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
from logicore.config import settings
from logicore.gateway.gateway import ProviderGateway, get_gateway_for_provider
from logicore.tools import ALL_TOOL_SCHEMAS, SAFE_TOOLS, TOOL_PRESETS, ALWAYS_ON_TOOLS
from logicore.tools.tool_names import ToolName
from logicore.agent.agent_config import AgentConfig
from logicore.agent.agent_protocol import AgentProtocol
from logicore.agent.agent_skills import AgentSkillsMixin
from logicore.agent.agent_sessions import AgentSessionsMixin, safe_file_id, guess_mime
from logicore.agent.agent_streaming import AgentStreamingMixin
from logicore.agent.agent_prompt import AgentPromptMixin
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


class Agent(AgentSkillsMixin, AgentSessionsMixin, AgentStreamingMixin, AgentPromptMixin, AgentProtocol):
    """
    A unified, modular AI Agent that supports:
    - Internal tools (filesystem, web, etc.)
    - External MCP tools (Excel, etc.)
    - Multi-session management
    - Custom tool registration
    
    Uses composable components for extensibility.
    
    Implements AgentProtocol to define the contract expected by ChatOrchestrator
    and other components.
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
        approval_timeout: float = 120.0,
        allow_tools: set = None,
        # Storage (optional — enables session persistence)
        storage=None,
        # Composable components (optional overrides)
        tool_executor: Optional[ToolExecutor] = None,
        chat_orchestrator: Optional[ChatOrchestrator] = None,
        input_enricher: Optional[InputEnricher] = None,
        # New: typed config object (takes precedence over positional args)
        config: Optional[AgentConfig] = None,
        # Verification: auto-verify created artifacts before returning to user
        verify_output: bool = False,
    ):
        # If config is provided, extract values from it (config wins over positional args)
        if config is not None:
            provider = config.provider if config.provider is not None else provider
            model = config.model if config.model is not None else model
            api_key = config.api_key if config.api_key is not None else api_key
            endpoint = config.endpoint if config.endpoint is not None else endpoint
            system_prompt = config.system_prompt if config.system_prompt is not None else system_prompt
            role = config.role
            debug = config.debug
            tools = config.tools if config.tools is not None else tools
            tool_preset = config.tool_preset if config.tool_preset is not None else tool_preset
            max_iterations = config.max_iterations
            telemetry = config.telemetry
            skills = config.skills if config.skills is not None else skills
            workspace_root = config.workspace_root if config.workspace_root is not None else workspace_root
            reasoning_level = config.reasoning_level
            plan_mode = config.plan_mode
            agent_id = config.agent_id if config.agent_id is not None else agent_id
            approval_timeout = config.approval_timeout
            allow_tools = config.allow_tools if config.allow_tools is not None else allow_tools
            storage = config.storage if config.storage is not None else storage
            tool_executor = config.tool_executor if config.tool_executor is not None else tool_executor
            chat_orchestrator = config.chat_orchestrator if config.chat_orchestrator is not None else chat_orchestrator
            input_enricher = config.input_enricher if config.input_enricher is not None else input_enricher
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
        
        # Verification: inject verification instructions into system prompt
        self.verify_output = verify_output
        if verify_output:
            try:
                from logicore.config.prompts import get_verification_instructions
                verification_instructions = get_verification_instructions()
                self.default_system_message += verification_instructions
                if self._custom_system_message:
                    self._custom_system_message += verification_instructions
            except ImportError:
                pass
        
        # Telemetry
        self.telemetry_enabled = telemetry
        self.telemetry_tracker = TelemetryTracker(enabled=telemetry)

        # Canonical token counters (always initialized — DB persistence needs them)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_api_calls = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "unknown"
        self.session_cost_source = "none"

        # Storage (optional session persistence)
        # Only created when explicitly provided. Pass storage=create_storage()
        # to enable session persistence. Without it, the agent runs stateless.
        self._storage = storage

        # Context engine
        from logicore.runtime.context import ContextEngine
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
        # Loop detection / recovery engine, wired into the chat loop via
        # ChatOrchestrator. Built once from the shared runtime config so the
        # agent can detect and recover from tool/content loops instead of
        # silently burning its iteration budget and dying with no explanation.
        try:
            from logicore.runtime.loop_detection import LoopDetectionEngine
            from logicore.runtime.turn_manager import TurnManager
            self._loop_engine = LoopDetectionEngine(_rt_config)
            self._turn_manager = TurnManager(_rt_config)
        except Exception as e:
            logger.debug(f"[Agent] Loop detection/recovery unavailable: {e}")
            self._loop_engine = None
            self._turn_manager = None

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
        from logicore.config import settings
        # Task/session/plan history lives under the config-controlled root,
        # never the cwd. workspace_root (if any) stays for project-scoped use.
        self._task_base_dir = str(settings.paths.tasks_dir)
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
                self._planner = PlanService(project_dir=None)
            except ImportError:
                pass

        # Tool Management
        self.internal_tools = []
        self.disabled_tools = set()
        self.workspace_root = workspace_root
        
        # Composable components
        # Fall back to env var so child subprocesses (e.g. bash tool running a
        # script that creates its own Agent) inherit the parent's timeout.
        if approval_timeout is None:
            _env_timeout = os.environ.get("LOGICORE_APPROVAL_TIMEOUT")
            if _env_timeout is not None:
                try:
                    approval_timeout = float(_env_timeout)
                except (ValueError, TypeError):
                    pass
        self.tool_executor = tool_executor or ToolExecutor(
            debug=debug, approval_timeout=approval_timeout, allow_tools=allow_tools,
        )
        self.input_enricher = input_enricher or InputEnricher(workspace_root=workspace_root, debug=debug)
        self._chat_orchestrator = chat_orchestrator or ChatOrchestrator(agent=self, debug=debug)
        
        # Verification: initialize orchestrator if enabled
        self._verification_orchestrator = None
        if verify_output:
            try:
                from logicore.verification.orchestrator import VerificationOrchestrator
                from logicore.verification.config import VerificationConfig
                self._verification_orchestrator = VerificationOrchestrator(VerificationConfig.from_env())
            except ImportError:
                logger.debug("[Agent] Verification module not available")
        
        # Skills Management
        self.skills: List[Skill] = []
        self._skill_tools_registered: set = set()  # Track which skills have tools registered
        
        # Session Management
        self.sessions: Dict[str, AgentSession] = {}
        self._session_locks: Dict[str, "asyncio.Lock"] = {}
        
        # Execution Tracking
        self.execution_log: List[str] = []
        
        # Tool support flag
        self.supports_tools = False
        self.tools_disabled_reason = None
        
        # Handle tools parameter
        if tools == []:
            # `tools=[]` disables all internal tools (filesystem, web, code
            # execution, git, cron, process mgmt, etc.) but keeps the
            # minimum structural set that every agent needs to decompose
            # complex work (task management, planning, load_skill) plus
            # skill metadata so the agent can discover and load skills on
            # demand.  Pass ``skills=[]`` to also suppress skill loading.
            self._load_structural_tools(load_skills=(skills != []))
        elif tool_preset and tool_preset in TOOL_PRESETS:
            self.load_tools_preset(tool_preset)
        elif isinstance(tools, list):
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
            # Always load default skills (instruction-based, no external tools needed)
            self._load_default_skills()
            # Auto-discover workspace skills (e.g. ~/.agents/skills/)
            self._load_workspace_skills()
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
        base = {
            "session_id": None,
            "model": self.model_name,
            "provider": getattr(self.provider, "provider_name", "unknown"),
            "input_tokens": self.session_input_tokens,
            "output_tokens": self.session_output_tokens,
            "cache_read_tokens": self.session_cache_read_tokens,
            "cache_write_tokens": self.session_cache_write_tokens,
            "reasoning_tokens": self.session_reasoning_tokens,
            "api_calls": self.session_api_calls,
            "total_tokens": self.session_input_tokens + self.session_output_tokens,
        }
        if not self.telemetry_enabled:
            return base
        session_ids = self.telemetry_tracker.get_session_ids()
        if len(session_ids) == 1:
            base["session_id"] = session_ids[0]
            base["tracker_summary"] = self.telemetry_tracker.get_session_summary(session_ids[0])
        elif len(session_ids) > 1:
            base["tracker_summary"] = self.telemetry_tracker.get_total_summary()
        return base

    @property
    def usage(self) -> dict:
        """Current canonical usage state for this agent session."""
        return {
            "input_tokens": self.session_input_tokens,
            "output_tokens": self.session_output_tokens,
            "cache_read_tokens": self.session_cache_read_tokens,
            "cache_write_tokens": self.session_cache_write_tokens,
            "reasoning_tokens": self.session_reasoning_tokens,
            "total_tokens": self.session_input_tokens + self.session_output_tokens,
            "api_calls": self.session_api_calls,
            "estimated_cost_usd": self.session_estimated_cost_usd,
            "cost_status": self.session_cost_status,
            "cost_source": self.session_cost_source,
        }

    # === Storage Persistence ===

    def _persist_session(self, session_id: str, response: str = "") -> None:
        """Persist session and telemetry to storage (non-blocking, best-effort)."""
        if not self._storage or not self._storage.initialized:
            return
        try:
            session_obj = self.sessions.get(session_id)
            if not session_obj:
                return
            provider_name = self.provider.__class__.__name__ if hasattr(self, "provider") else ""

            # Persist VFS files to Tier-3 assets folder (binary bytes); SQL keeps the
            # filename -> path mapping so restore can rehydrate session.files.
            file_refs = []
            if session_obj.files:
                for fname, content in session_obj.files.items():
                    file_id = safe_file_id(fname)
                    data = content.encode("utf-8") if isinstance(content, str) else content
                    mime = guess_mime(fname)
                    info = self._storage.save_attachment(session_id, file_id, data, mime)
                    file_refs.append({"name": fname, "path": info.path, "mime": info.mime})

            # Merge file refs into metadata (stored in SQL context column)
            meta = dict(session_obj.metadata) if session_obj.metadata else {}
            if file_refs:
                meta["_vfs_files"] = file_refs

            self._storage.save_session(
                session_id, session_obj.messages,
                provider=provider_name, model=getattr(self, "model_name", ""),
                metadata=meta if meta else None,
            )
            self._storage.save_telemetry(
                session_id,
                input_tokens=self.session_input_tokens,
                output_tokens=self.session_output_tokens,
                cache_read_tokens=self.session_cache_read_tokens,
                cache_write_tokens=self.session_cache_write_tokens,
                reasoning_tokens=self.session_reasoning_tokens,
                api_calls=self.session_api_calls,
            )
        except Exception as e:
            if self.debug:
                logger.warning(f"[Agent] Storage persistence failed: {e}")

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

    # === Tool Management ===
    
    def _load_tools(self, mode: str = "default", preset: str = None, load_skills: bool = True):
        """Consolidated tool loading — single entry point for all tool loading paths.

        Args:
            mode: "default" (all tools), "preset" (use preset name), "structural" (ALWAYS_ON only)
            preset: Preset name when mode="preset" (e.g., "smart", "copilot", "minimal")
            load_skills: Whether to load skill metadata
        """
        from logicore.tasks import get_task_tools, get_task_tool_schemas, get_task_tools_with_context

        self._ensure_task_manager()

        # 1. Register task tool executors
        tools = (get_task_tools_with_context(self._task_tool_context)
                 if self._task_tool_context else get_task_tools())
        for tool in tools:
            self.tool_executor.register_custom_tool(tool.name, tool.run)

        # 2. Load tool schemas based on mode
        if mode == "structural":
            from logicore.tools.registry import ALWAYS_ON_TOOLS, ToolRegistry
            temp_registry = ToolRegistry(enabled_tools=ALWAYS_ON_TOOLS)
            self.internal_tools.extend(temp_registry.schemas)
        elif mode == "preset" and preset:
            from logicore.tools.registry import ToolRegistry
            preset_tools = TOOL_PRESETS.get(preset, [])
            temp_registry = ToolRegistry(enabled_tools=preset_tools)
            # Avoid duplicating task-tool schemas
            loaded_names = {t.get("function", {}).get("name") for t in temp_registry.schemas}
            task_schemas = get_task_tool_schemas()
            if not any(s.get("function", {}).get("name") in loaded_names for s in task_schemas):
                self.internal_tools.extend(task_schemas)
            self.internal_tools.extend(temp_registry.schemas)
        else:  # "default"
            # Avoid duplicating task-tool schemas
            loaded_names = {t.get("function", {}).get("name") for t in ALL_TOOL_SCHEMAS}
            task_schemas = get_task_tool_schemas()
            if not any(s.get("function", {}).get("name") in loaded_names for s in task_schemas):
                self.internal_tools.extend(task_schemas)
            self.internal_tools.extend(ALL_TOOL_SCHEMAS)

        self.supports_tools = True

        # 3. Load skills
        if load_skills:
            if mode == "preset" and preset in ("minimal", "lightweight"):
                pass  # Skip skills for minimal presets
            else:
                self._load_default_skills()
                self._load_workspace_skills()

        # 4. Rebuild prompt
        self._rebuild_system_prompt_with_tools()

    def _load_structural_tools(self, load_skills=True):
        """Load the minimum tool set that every agent needs."""
        self._load_tools(mode="structural", load_skills=load_skills)

    def load_default_tools(self):
        self._load_tools(mode="default")
    
    def load_tools_preset(self, preset: str):
        if preset == "full":
            self.load_default_tools()
            return
        if preset not in TOOL_PRESETS:
            return
        self._load_tools(mode="preset", preset=preset)
    
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
        has_cron = ToolName.ADD_CRON_JOB in tool_names
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
        file_tools = {ToolName.READ_FILE, ToolName.EDIT_FILE, ToolName.CREATE_FILE, ToolName.DELETE_FILE}
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
        if tool_name == ToolName.CREATE_FILE or os.path.exists(candidate):
            normalized = args.copy()
            normalized["file_path"] = candidate
            return normalized
        return args

    def _update_tool_directory_context(self, session: AgentSession, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]):
        if not isinstance(result, dict) or not bool(result.get("success")):
            return
        if tool_name == ToolName.LIST_FILES:
            directory = args.get("directory") if isinstance(args, dict) else None
            if isinstance(directory, str) and directory.strip() and directory.strip() != ".":
                session.metadata["last_tool_directory"] = os.path.normpath(directory.strip())
            return
        if tool_name in {ToolName.CREATE_FILE, ToolName.READ_FILE, ToolName.EDIT_FILE, ToolName.DELETE_FILE} and isinstance(args, dict):
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
                workspace_root=str(settings.paths.sessions_dir),
                session_id=session_id,
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

    def set_approval_timeout(self, timeout: Optional[float]) -> None:
        """Set the maximum seconds to wait for a tool approval decision.

        Args:
            timeout: Seconds to wait before returning a structured
                ``needs_approval`` result.  ``None`` disables the timeout
                (wait forever — the original interactive behaviour).
        """
        self.tool_executor.approval_timeout = timeout

    def set_allow_tools(self, tools: set) -> None:
        """Pre-authorise specific tools so they skip the approval check.

        When the agent runs a child process (e.g. via ``bash``), the
        child's Agent picks up the same allow-list via
        ``LOGICORE_ALLOW_TOOLS`` env var, so those tools execute without
        prompting — solving the deadlock where a child script's tool
        calls hang waiting for approval that never comes.

        Args:
            tools: Set of tool names to pre-authorise (e.g.
                ``{"get_order_status", "create_support_ticket"}``).
        """
        self.tool_executor.set_allow_tools(tools)

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
        # Stop the snapshot worker so its thread doesn't block process exit
        if self._storage and hasattr(self._storage, 'close'):
            try:
                self._storage.close(drain_timeout=2.0)
            except Exception:
                pass
