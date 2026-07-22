from typing import Dict, Any, List, Optional, Set
import logging

from .base import BaseTool, ToolResult
from .tool_names import ToolName

logger = logging.getLogger(__name__)
from .filesystem import (
    ReadFileTool, CreateFileTool, EditFileTool, DeleteFileTool, 
    ListFilesTool, SearchFilesTool, FastGrepTool
)
from .execution import (
    ExecuteCommandTool, CodeExecuteTool, 
    ListProcessesTool, KillProcessTool, GetProcessInfoTool,
    GetProcessOutputTool, TailProcessOutputTool, WatchProcessTool
)
from .web import WebSearchTool, UrlFetchTool, ImageSearchTool
from .git import GitCommandTool
from .document import ReadDocumentTool
from .convert import ConvertDocumentTool
from .notes import NotesTool
from .datetime import DateTimeTool
from .think import ThinkTool
from .bash import SmartBashTool
from .media import MediaSearchTool
from .cron import AddCronJobTool, ListCronJobsTool, RemoveCronJobTool, GetCronsTool
from .plan import (
    EnterPlanModeTool, SubmitPlanTool, ExitPlanModeTool,
    UpdatePlanProgressTool, ViewPlanTool
)
from .skill_loader import LoadSkillTool

# Tool presets for different use cases
TOOL_PRESETS = {
    "lightweight": [
        ToolName.READ_FILE, ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.LIST_FILES,
        ToolName.SEARCH_FILES, ToolName.FAST_GREP, ToolName.EXECUTE_COMMAND, ToolName.CODE_EXECUTE,
        ToolName.LIST_PROCESSES, ToolName.KILL_PROCESS, ToolName.GET_PROCESS_INFO,
        ToolName.GET_PROCESS_OUTPUT, ToolName.TAIL_PROCESS_OUTPUT, ToolName.WATCH_PROCESS,
        ToolName.WEB_SEARCH, ToolName.GIT_COMMAND
    ],
    "smart": [
        # SmartAgent core tools - essential agentic toolkit
        ToolName.BASH, ToolName.DATETIME, ToolName.NOTES, ToolName.THINK,
        # Filesystem
        ToolName.READ_FILE, ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.DELETE_FILE,
        ToolName.LIST_FILES, ToolName.SEARCH_FILES, ToolName.FAST_GREP,
        # Execution
        ToolName.CODE_EXECUTE,
        # Process management
        ToolName.LIST_PROCESSES, ToolName.KILL_PROCESS, ToolName.GET_PROCESS_OUTPUT,
        # Web
        ToolName.WEB_SEARCH, ToolName.IMAGE_SEARCH,
        # Cron
        ToolName.ADD_CRON_JOB, ToolName.LIST_CRON_JOBS, ToolName.REMOVE_CRON_JOB, ToolName.GET_CRONS,
        # Document
        ToolName.READ_DOCUMENT, ToolName.CONVERT_DOCUMENT,
        # Git
        ToolName.GIT_COMMAND,
        # Media
        ToolName.MEDIA_SEARCH,
        # V2 Task Management
        ToolName.TASK_CREATE, ToolName.TASK_GET, ToolName.TASK_UPDATE, ToolName.TASK_LIST, ToolName.TASK_NEXT,
        # Plan
        ToolName.ENTER_PLAN_MODE, ToolName.SUBMIT_PLAN, ToolName.VIEW_PLAN,
        # Skill Management
        ToolName.LOAD_SKILL,
    ],
    "copilot": [
        # Copilot - coding focused with filesystem and execution
        ToolName.READ_FILE, ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.DELETE_FILE,
        ToolName.LIST_FILES, ToolName.SEARCH_FILES, ToolName.FAST_GREP,
        ToolName.EXECUTE_COMMAND, ToolName.CODE_EXECUTE,
        ToolName.LIST_PROCESSES, ToolName.KILL_PROCESS, ToolName.GET_PROCESS_OUTPUT,
        ToolName.GIT_COMMAND,
        ToolName.WEB_SEARCH, ToolName.URL_FETCH,
        # V2 Task Management
        ToolName.TASK_CREATE, ToolName.TASK_GET, ToolName.TASK_UPDATE, ToolName.TASK_LIST, ToolName.TASK_NEXT,
    ],
    "full": "__all__",  # Load all tools
    "minimal": [
        ToolName.READ_FILE, ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.LIST_FILES,
        ToolName.EXECUTE_COMMAND, ToolName.CODE_EXECUTE
    ],
    "webdev": [
        ToolName.READ_FILE, ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.LIST_FILES,
        ToolName.SEARCH_FILES, ToolName.FAST_GREP, ToolName.EXECUTE_COMMAND, ToolName.CODE_EXECUTE,
        ToolName.LIST_PROCESSES, ToolName.KILL_PROCESS, ToolName.GET_PROCESS_OUTPUT,
        ToolName.WEB_SEARCH, ToolName.URL_FETCH
    ],
}

class ToolRegistry:
    def __init__(self, preset: Optional[str] = None, enabled_tools: Optional[List[str]] = None, disabled_tools: Optional[List[str]] = None):
        """
        Initialize ToolRegistry with optional tool filtering.
        
        Args:
            preset: Use a predefined tool preset ("lightweight", "smart", "copilot", "full", "minimal", "webdev")
            enabled_tools: List of tool names to enable (overrides preset)
            disabled_tools: List of tool names to disable (applied after preset/enabled_tools)
        """
        self._tools: Dict[str, BaseTool] = {}
        
        # Store all tool classes for lazy registration
        # Import task tools from the tasks module
        from logicore.tasks.tools import TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool, TaskNextTool
        
        all_tool_classes = {
            # Task Management (registered first for prompt priority)
            ToolName.TASK_CREATE: TaskCreateTool,
            ToolName.TASK_GET: TaskGetTool,
            ToolName.TASK_UPDATE: TaskUpdateTool,
            ToolName.TASK_LIST: TaskListTool,
            ToolName.TASK_NEXT: TaskNextTool,
            # Filesystem
            ToolName.READ_FILE: ReadFileTool,
            ToolName.CREATE_FILE: CreateFileTool,
            ToolName.EDIT_FILE: EditFileTool,
            ToolName.DELETE_FILE: DeleteFileTool,
            ToolName.LIST_FILES: ListFilesTool,
            ToolName.SEARCH_FILES: SearchFilesTool,
            ToolName.FAST_GREP: FastGrepTool,
            # Execution
            ToolName.EXECUTE_COMMAND: ExecuteCommandTool,
            ToolName.CODE_EXECUTE: CodeExecuteTool,
            # Process management
            ToolName.LIST_PROCESSES: ListProcessesTool,
            ToolName.KILL_PROCESS: KillProcessTool,
            ToolName.GET_PROCESS_INFO: GetProcessInfoTool,
            ToolName.GET_PROCESS_OUTPUT: GetProcessOutputTool,
            ToolName.TAIL_PROCESS_OUTPUT: TailProcessOutputTool,
            ToolName.WATCH_PROCESS: WatchProcessTool,
            # Web
            ToolName.WEB_SEARCH: WebSearchTool,
            ToolName.IMAGE_SEARCH: ImageSearchTool,
            ToolName.URL_FETCH: UrlFetchTool,
            # Git
            ToolName.GIT_COMMAND: GitCommandTool,
            # Document
            ToolName.READ_DOCUMENT: ReadDocumentTool,
            ToolName.CONVERT_DOCUMENT: ConvertDocumentTool,
            # Media
            ToolName.MEDIA_SEARCH: MediaSearchTool,
            # Cron
            ToolName.ADD_CRON_JOB: AddCronJobTool,
            ToolName.LIST_CRON_JOBS: ListCronJobsTool,
            ToolName.REMOVE_CRON_JOB: RemoveCronJobTool,
            ToolName.GET_CRONS: GetCronsTool,
            # SmartAgent specific tools
            ToolName.BASH: SmartBashTool,
            ToolName.DATETIME: DateTimeTool,
            ToolName.NOTES: NotesTool,
            ToolName.THINK: ThinkTool,
            # Plan
            ToolName.ENTER_PLAN_MODE: EnterPlanModeTool,
            ToolName.SUBMIT_PLAN: SubmitPlanTool,
            ToolName.EXIT_PLAN_MODE: ExitPlanModeTool,
            ToolName.UPDATE_PLAN_PROGRESS: UpdatePlanProgressTool,
            ToolName.VIEW_PLAN: ViewPlanTool,
            # Skill Management
            ToolName.LOAD_SKILL: LoadSkillTool,
        }
        
        # Determine which tools to register
        if enabled_tools is not None:
            # Explicit list of tools to enable
            tools_to_register = set(enabled_tools)
        elif preset and preset in TOOL_PRESETS:
            preset_value = TOOL_PRESETS[preset]
            if preset_value == "__all__":
                tools_to_register = set(all_tool_classes.keys())
            else:
                tools_to_register = set(preset_value)
        else:
            # Default: register all tools (backward compatibility)
            tools_to_register = set(all_tool_classes.keys())
        
        # Apply disabled_tools filter
        if disabled_tools:
            tools_to_register -= set(disabled_tools)
        
        # Register the selected tools
        for tool_name in tools_to_register:
            if tool_name in all_tool_classes:
                self.register_tool(all_tool_classes[tool_name]())

    def register_tool(self, tool: BaseTool):
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} already registered.")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool:
        return self._tools.get(name)

    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
        tool = self.get_tool(tool_name)
        if tool is None:
            # Unknown-tool failures are deterministic — never retryable.
            from logicore.tools.error_classifier import (
                ClassifiedToolError, RecoveryAction, ToolFailoverReason,
            )
            classified = ClassifiedToolError(
                reason=ToolFailoverReason.not_found,
                recovery_action=RecoveryAction.INJECT_SIGNAL,
                message=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
                retryable=False,
            )
            return ToolResult(
                success=False, error=classified.message,
                **{"error_category": classified.reason.value,
                   "recovery_action": classified.recovery_action.value,
                   "retryable": classified.retryable,
                   "should_rotate_credential": classified.should_rotate_credential},
            )

        try:
            # Pydantic validation
            validated_args = tool.args_schema(**tool_args).model_dump()
            return tool.run(**validated_args)
        except Exception as e:
            # Mirror the hermes-agent parent's structured tool-error taxonomy so
            # the agent loop can retry transient failures / rotate credentials.
            from logicore.tools.error_classifier import classify_tool_error
            classified = classify_tool_error(e, tool_name=tool_name, is_credentialed=False)
            logger.warning(
                "Tool %s execution failed [%s, recovery=%s, retryable=%s]: %s",
                tool_name, classified.reason.value, classified.recovery_action.value,
                classified.retryable, e,
            )
            return ToolResult(
                success=False, error=classified.message,
                **{"error_category": classified.reason.value,
                   "recovery_action": classified.recovery_action.value,
                   "retryable": classified.retryable,
                   "should_rotate_credential": classified.should_rotate_credential},
            )

    @property
    def schemas(self) -> List[Dict[str, Any]]:
        return [tool.schema for tool in self._tools.values()]
    
    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())
    
    def has_tool(self, name: str) -> bool:
        return name in self._tools

# Global registry instance (default: all tools)
registry = ToolRegistry()

# Preset registries
lightweight_registry = ToolRegistry(preset="lightweight")
smart_registry = ToolRegistry(preset="smart")
copilot_registry = ToolRegistry(preset="copilot")

def execute_tool(tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
    return registry.execute_tool(tool_name, tool_args)

def execute_tool_lightweight(tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
    return lightweight_registry.execute_tool(tool_name, tool_args)

def execute_tool_smart(tool_name: str, tool_args: Dict[str, Any]) -> ToolResult:
    return smart_registry.execute_tool(tool_name, tool_args)

# Tool categories
SAFE_TOOLS = [
    ToolName.READ_FILE, ToolName.LIST_FILES, ToolName.SEARCH_FILES, ToolName.FAST_GREP,
    ToolName.READ_DOCUMENT, ToolName.MEDIA_SEARCH, ToolName.LIST_CRON_JOBS, ToolName.GET_CRONS,
    # Task management tools (safe, internal bookkeeping - no approval needed)
    ToolName.TASK_CREATE, ToolName.TASK_GET, ToolName.TASK_UPDATE, ToolName.TASK_LIST, ToolName.TASK_NEXT,
    # Skill management (read-only, loads instructions into context)
    ToolName.LOAD_SKILL,
]
APPROVAL_REQUIRED_TOOLS = [
    ToolName.CREATE_FILE, ToolName.EDIT_FILE, ToolName.WEB_SEARCH, ToolName.IMAGE_SEARCH,
    ToolName.URL_FETCH, ToolName.CONVERT_DOCUMENT,
    ToolName.ADD_CRON_JOB, ToolName.REMOVE_CRON_JOB
]
DANGEROUS_TOOLS = [ToolName.DELETE_FILE, ToolName.EXECUTE_COMMAND, ToolName.GIT_COMMAND, ToolName.CODE_EXECUTE]

# Tools that must ALWAYS be available regardless of `tools=[]` or other
# opt-out flags.  These give the agent the minimum ability to decompose
# complex work (task management), plan multi-step flows, and discover
# skills on demand.
ALWAYS_ON_TOOLS = [
    # Task management — agent needs these to track work
    ToolName.TASK_CREATE, ToolName.TASK_GET, ToolName.TASK_UPDATE, ToolName.TASK_LIST, ToolName.TASK_NEXT,
    # Planning — agent needs these for complex multi-step tasks
    ToolName.ENTER_PLAN_MODE, ToolName.SUBMIT_PLAN, ToolName.EXIT_PLAN_MODE,
    ToolName.UPDATE_PLAN_PROGRESS, ToolName.VIEW_PLAN,
    # Skill loading — agent needs this to load skills on demand
    ToolName.LOAD_SKILL,
]
