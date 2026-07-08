from typing import Dict, Any, List, Optional, Set
import logging

from .base import BaseTool, ToolResult

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
from .pdf import MergePDFTool, SplitPDFTool
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

# Tool presets for different use cases
TOOL_PRESETS = {
    "lightweight": [
        "read_file", "create_file", "edit_file", "list_files",
        "search_files", "fast_grep", "execute_command", "code_execute",
        "list_processes", "kill_process", "get_process_info",
        "get_process_output", "tail_process_output", "watch_process",
        "web_search", "git_command"
    ],
    "smart": [
        # SmartAgent core tools - essential agentic toolkit
        "bash", "datetime", "notes", "think",
        # Filesystem
        "read_file", "create_file", "edit_file", "delete_file",
        "list_files", "search_files", "fast_grep",
        # Execution
        "code_execute",
        # Process management
        "list_processes", "kill_process", "get_process_output",
        # Web
        "web_search", "image_search",
        # Cron
        "add_cron_job", "list_cron_jobs", "remove_cron_job", "get_crons",
        # Document
        "read_document", "convert_document",
        # Git
        "git_command",
        # Media
        "media_search",
        # V2 Task Management
        "task_create", "task_get", "task_update", "task_list", "task_next",
        # Plan
        "enter_plan_mode", "submit_plan", "view_plan",
    ],
    "copilot": [
        # Copilot - coding focused with filesystem and execution
        "read_file", "create_file", "edit_file", "delete_file",
        "list_files", "search_files", "fast_grep",
        "execute_command", "code_execute",
        "list_processes", "kill_process", "get_process_output",
        "git_command",
        "web_search", "url_fetch",
        # V2 Task Management
        "task_create", "task_get", "task_update", "task_list", "task_next",
    ],
    "full": "__all__",  # Load all tools
    "minimal": [
        "read_file", "create_file", "edit_file", "list_files",
        "execute_command", "code_execute"
    ],
    "webdev": [
        "read_file", "create_file", "edit_file", "list_files",
        "search_files", "fast_grep", "execute_command", "code_execute",
        "list_processes", "kill_process", "get_process_output",
        "web_search", "url_fetch"
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
            "task_create": TaskCreateTool,
            "task_get": TaskGetTool,
            "task_update": TaskUpdateTool,
            "task_list": TaskListTool,
            "task_next": TaskNextTool,
            # Filesystem
            "read_file": ReadFileTool,
            "create_file": CreateFileTool,
            "edit_file": EditFileTool,
            "delete_file": DeleteFileTool,
            "list_files": ListFilesTool,
            "search_files": SearchFilesTool,
            "fast_grep": FastGrepTool,
            # Execution
            "execute_command": ExecuteCommandTool,
            "code_execute": CodeExecuteTool,
            # Process management
            "list_processes": ListProcessesTool,
            "kill_process": KillProcessTool,
            "get_process_info": GetProcessInfoTool,
            "get_process_output": GetProcessOutputTool,
            "tail_process_output": TailProcessOutputTool,
            "watch_process": WatchProcessTool,
            # Web
            "web_search": WebSearchTool,
            "image_search": ImageSearchTool,
            "url_fetch": UrlFetchTool,
            # Git
            "git_command": GitCommandTool,
            # Document
            "read_document": ReadDocumentTool,
            "convert_document": ConvertDocumentTool,
            # Media
            "media_search": MediaSearchTool,
            # PDF
            "merge_pdfs": MergePDFTool,
            "split_pdf": SplitPDFTool,
            # Cron
            "add_cron_job": AddCronJobTool,
            "list_cron_jobs": ListCronJobsTool,
            "remove_cron_job": RemoveCronJobTool,
            "get_crons": GetCronsTool,
            # SmartAgent specific tools
            "bash": SmartBashTool,
            "datetime": DateTimeTool,
            "notes": NotesTool,
            "think": ThinkTool,
            # Plan
            "enter_plan_mode": EnterPlanModeTool,
            "submit_plan": SubmitPlanTool,
            "exit_plan_mode": ExitPlanModeTool,
            "update_plan_progress": UpdatePlanProgressTool,
            "view_plan": ViewPlanTool,
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
                ClassifiedToolError, ToolFailoverReason,
            )
            classified = ClassifiedToolError(
                reason=ToolFailoverReason.not_found,
                message=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
                retryable=False,
            )
            return ToolResult(
                success=False, error=classified.message,
                **{"error_category": classified.reason.value,
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
                "Tool %s execution failed [%s, retryable=%s]: %s",
                tool_name, classified.reason.value, classified.retryable, e,
            )
            return ToolResult(
                success=False, error=classified.message,
                **{"error_category": classified.reason.value,
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
    'read_file', 'list_files', 'search_files', 'fast_grep', 
    'read_document', 'media_search', 'list_cron_jobs', 'get_crons',
    # Task management tools (safe, internal bookkeeping - no approval needed)
    'task_create', 'task_get', 'task_update', 'task_list', 'task_next',
]
APPROVAL_REQUIRED_TOOLS = [
    'create_file', 'edit_file', 'web_search', 'image_search', 'url_fetch', 'convert_document',
    'merge_pdfs', 'split_pdf',
    'add_cron_job', 'remove_cron_job'
]
DANGEROUS_TOOLS = ['delete_file', 'execute_command', 'git_command', 'code_execute']
