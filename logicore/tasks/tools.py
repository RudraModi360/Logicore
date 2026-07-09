"""
Agent tools for task management.

Tools:
- task_create: Create a new task
- task_get: Get task details and claim it
- task_update: Update task status/fields
- task_list: List tasks with filtering
- task_next: Get next available task

These tools wrap TaskManager and are registered in the tool registry.

Supports dependency injection for multi-agent scenarios:
- TaskToolContext: Holds task manager and agent ID for each agent instance
- get_task_tools_with_context(): Returns tools bound to a specific context
"""

from typing import Optional, List, TYPE_CHECKING
from pydantic import BaseModel, Field
from logicore.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from logicore.tasks.manager import TaskManager


# === Dependency Injection Context ===

class TaskToolContext:
    """
    Context for task tools that holds task manager and agent ID.
    
    This enables multi-agent scenarios by eliminating global state.
    Each agent instance should create its own context.
    
    Usage:
        context = TaskToolContext(task_manager=manager, agent_id="agent-1")
        tools = get_task_tools_with_context(context)
        for tool in tools:
            agent.tool_executor.register_custom_tool(tool.name, tool.run)
    """
    
    def __init__(self, task_manager: 'TaskManager', agent_id: str):
        """
        Initialize task tool context.
        
        Args:
            task_manager: The TaskManager instance for this agent
            agent_id: Unique identifier for this agent
        """
        self.task_manager = task_manager
        self.agent_id = agent_id
    
    def get_task_manager(self) -> 'TaskManager':
        """Get the task manager for this context."""
        return self.task_manager
    
    def get_agent_id(self) -> str:
        """Get the agent ID for this context."""
        return self.agent_id


# === Tool Argument Schemas ===

class TaskCreateArgs(BaseModel):
    """Arguments for task_create tool."""
    subject: str = Field(
        description="Short title for the task (e.g., 'Implement login page')"
    )
    description: str = Field(
        default="",
        description="Detailed description of what needs to be done"
    )
    active_form: Optional[str] = Field(
        default=None,
        description="Continuous verb form for UI display (e.g., 'Running tests', 'Building feature')"
    )
    blocked_by: Optional[List[str]] = Field(
        default=None,
        description="List of task IDs this task depends on (blocks until those complete)"
    )


class TaskGetArgs(BaseModel):
    """Arguments for task_get tool."""
    task_id: str = Field(
        description="ID of the task to get"
    )
    claim: bool = Field(
        default=False,
        description="Set to True to claim this task (assign ownership to yourself)"
    )


class TaskUpdateArgs(BaseModel):
    """Arguments for task_update tool."""
    task_id: str = Field(
        description="ID of the task to update"
    )
    status: Optional[str] = Field(
        default=None,
        description="New status: 'pending', 'in_progress', 'completed', 'failed'"
    )
    active_form: Optional[str] = Field(
        default=None,
        description="Update the active form display text"
    )
    subject: Optional[str] = Field(
        default=None,
        description="Update the task subject/title"
    )
    description: Optional[str] = Field(
        default=None,
        description="Update the task description"
    )


class TaskListArgs(BaseModel):
    """Arguments for task_list tool."""
    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'pending', 'in_progress', 'completed', 'failed'"
    )
    available_only: bool = Field(
        default=False,
        description="Only show tasks available for claiming (pending, unclaimed, unblocked)"
    )


class TaskNextArgs(BaseModel):
    """Arguments for task_next tool."""
    pass


# === Tool Implementations ===

# These tools are intercepted by the Agent before execution.
# They use TaskManager internally, which is initialized by the Agent.
#
# WARNING: These are module-level globals. If multiple Agent instances
# Context-local storage for task manager and agent ID.
# Uses contextvars so each async task/thread gets its own isolated state,
# preventing crosstalk between concurrent agents in the same process.

import logging as _logging
from contextvars import ContextVar

_task_logger = _logging.getLogger(__name__)

# Context variables for per-task state isolation
_task_manager_var: ContextVar = ContextVar('task_manager', default=None)
_agent_id_var: ContextVar = ContextVar('agent_id', default=None)


def set_task_manager(manager, owner_id: str = "unknown"):
    """Set the task manager instance for the current context.

    Args:
        manager: The TaskManager instance
        owner_id: Identifier of the agent (for logging/debugging)
    """
    _task_manager_var.set(manager)


def get_task_manager():
    """Get the task manager instance for the current context."""
    return _task_manager_var.get()


def set_agent_id(agent_id: str, owner_id: str = "unknown"):
    """Set the agent ID for the current context.

    Args:
        agent_id: The agent identifier
        owner_id: Identifier of the agent (for logging/debugging)
    """
    _agent_id_var.set(agent_id)


def get_agent_id() -> str:
    """Get the agent ID for the current context."""
    return _agent_id_var.get() or "agent"


class TaskCreateTool(BaseTool):
    """Create a new task in the task list."""
    name = "task_create"
    description = (
        "MUST USE for any request with 3+ steps. Creates a task to track work. "
        "For exploration tasks: explore first, then create tasks from findings. "
        "For implementation tasks: create tasks first, then execute. Use active_form "
        "for live UI status. Use blocked_by for dependencies."
    )
    args_schema = TaskCreateArgs
    
    def __init__(self, context: Optional[TaskToolContext] = None):
        """
        Initialize task create tool.
        
        Args:
            context: Optional context for dependency injection. If None, uses global state.
        """
        super().__init__()
        self._context = context
    
    def is_read_only(self, args=None) -> bool:
        """Task creation is NOT read-only (creates data)."""
        return False
    
    def is_destructive(self, args=None) -> bool:
        """Task creation is NOT destructive (reversible)."""
        return False
    
    def run(self, subject: str, description: str = "", active_form: Optional[str] = None,
            blocked_by: Optional[List[str]] = None, **kwargs) -> ToolResult:
        # Get task manager from context or global state
        task_manager = self._context.get_task_manager() if self._context else _task_manager
        if not task_manager:
            return ToolResult(success=False, error="Task manager not initialized. Call task_create to start.")
        try:
            task = task_manager.create_task(
                subject=subject,
                description=description,
                active_form=active_form,
                blocked_by=blocked_by,
            )
            return ToolResult(
                success=True,
                content={"task_id": task.id, "subject": task.subject, "status": task.status.value}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to create task: {str(e)}. Check parameters.")


class TaskGetTool(BaseTool):
    """Get task details and optionally claim it."""
    name = "task_get"
    description = (
        "ALWAYS USE after task_create. Get details of a task by ID and claim it "
        "to start working. Claiming assigns ownership. Use claim=true to take "
        "ownership of a task before working on it."
    )
    args_schema = TaskGetArgs
    
    def __init__(self, context: Optional[TaskToolContext] = None):
        """
        Initialize task get tool.
        
        Args:
            context: Optional context for dependency injection. If None, uses global state.
        """
        super().__init__()
        self._context = context
    
    def is_read_only(self, args=None) -> bool:
        """Task get is read-only when not claiming (just reads data)."""
        if args and args.get('claim'):
            return False  # Claiming modifies data
        return True
    
    def is_destructive(self, args=None) -> bool:
        """Task get is NOT destructive."""
        return False
    
    def run(self, task_id: str, claim: bool = False, **kwargs) -> ToolResult:
        # Get task manager and agent ID from context or global state
        task_manager = self._context.get_task_manager() if self._context else _task_manager
        agent_id = self._context.get_agent_id() if self._context else get_agent_id()
        
        if not task_manager:
            return ToolResult(success=False, error="Task manager not initialized. Call task_create first.")
        try:
            if claim:
                task = task_manager.claim_task(task_id, agent_id=agent_id, check_agent_busy=False)
                return ToolResult(
                    success=True,
                    content={**task.to_dict(), "claimed": True}
                )
            else:
                task = task_manager.store.get(task_id)
                if not task:
                    return ToolResult(success=False, error=f"Task #{task_id} not found. Use task_list to see available tasks.")
                return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to get task: {str(e)}. Check task_id.")


class TaskUpdateTool(BaseTool):
    """Update task status and fields."""
    name = "task_update"
    description = (
        "ALWAYS USE when finishing work. Update task status to 'completed' when "
        "done, 'in_progress' when starting, or 'failed' if blocked. Setting "
        "status to 'completed' will cascade-unblock dependent tasks."
    )
    args_schema = TaskUpdateArgs
    
    def __init__(self, context: Optional[TaskToolContext] = None):
        """
        Initialize task update tool.
        
        Args:
            context: Optional context for dependency injection. If None, uses global state.
        """
        super().__init__()
        self._context = context
    
    def is_read_only(self, args=None) -> bool:
        """Task update is NOT read-only (modifies data)."""
        return False
    
    def is_destructive(self, args=None) -> bool:
        """Task update is NOT destructive (reversible)."""
        return False
    
    def run(self, task_id: str, status: Optional[str] = None,
            active_form: Optional[str] = None, subject: Optional[str] = None,
            description: Optional[str] = None, **kwargs) -> ToolResult:
        # Get task manager from context or global state
        task_manager = self._context.get_task_manager() if self._context else _task_manager
        if not task_manager:
            return ToolResult(success=False, error="Task manager not initialized. Call task_create first.")
        try:
            if status == "completed":
                task = task_manager.complete_task(task_id)
            elif status == "failed":
                task = task_manager.fail_task(task_id)
            else:
                task = task_manager.update_task(
                    task_id,
                    status=status,
                    active_form=active_form,
                    subject=subject,
                    description=description,
                )
            return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to update task: {str(e)}. Check task_id and status.")


class TaskListTool(BaseTool):
    """List tasks with filtering."""
    name = "task_list"
    description = (
        "List tasks in the task list. Can filter by status or show only "
        "available tasks (pending, unclaimed, unblocked)."
    )
    args_schema = TaskListArgs
    
    def __init__(self, context: Optional[TaskToolContext] = None):
        """
        Initialize task list tool.
        
        Args:
            context: Optional context for dependency injection. If None, uses global state.
        """
        super().__init__()
        self._context = context
    
    def is_read_only(self, args=None) -> bool:
        """Task list is read-only (just reads data)."""
        return True
    
    def is_destructive(self, args=None) -> bool:
        """Task list is NOT destructive."""
        return False
    
    def run(self, status: Optional[str] = None, available_only: bool = False, **kwargs) -> ToolResult:
        # Get task manager from context or global state
        task_manager = self._context.get_task_manager() if self._context else _task_manager
        if not task_manager:
            return ToolResult(success=False, error="Task manager not initialized. Call task_create first.")
        try:
            if available_only:
                tasks = task_manager.store.list_available()
            elif status:
                tasks = task_manager.store.list_by_status(status)
            else:
                tasks = task_manager.store.list_all()
            
            summary = task_manager.get_task_summary()
            return ToolResult(
                success=True,
                content={
                    "tasks": [t.to_dict() for t in tasks],
                    "summary": summary,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to list tasks: {str(e)}")


class TaskNextTool(BaseTool):
    """Get the next available task."""
    name = "task_next"
    description = (
        "USE after creating tasks. Returns the next pending, unclaimed, unblocked "
        "task. After getting the next task, call task_get with claim=true to take "
        "ownership before working on it."
    )
    args_schema = TaskNextArgs
    
    def __init__(self, context: Optional[TaskToolContext] = None):
        """
        Initialize task next tool.
        
        Args:
            context: Optional context for dependency injection. If None, uses global state.
        """
        super().__init__()
        self._context = context
    
    def is_read_only(self, args=None) -> bool:
        """Task next is read-only (just reads data)."""
        return True
    
    def is_destructive(self, args=None) -> bool:
        """Task next is NOT destructive."""
        return False
    
    def run(self, **kwargs) -> ToolResult:
        # Get task manager from context or global state
        task_manager = self._context.get_task_manager() if self._context else _task_manager
        if not task_manager:
            return ToolResult(success=False, error="Task manager not initialized. Call task_create first.")
        try:
            task = task_manager.get_next_task()
            if not task:
                return ToolResult(
                    success=True,
                    content={"message": "No available tasks. All tasks are completed or claimed.", "task": None}
                )
            return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to get next task: {str(e)}")


def get_task_tools(context: Optional[TaskToolContext] = None):
    """
    Get all task tools.
    
    Args:
        context: Optional context for dependency injection. If provided, tools will use
                 this context instead of global state. This enables multi-agent scenarios.
    
    Returns:
        List of task tool instances
    """
    return [
        TaskCreateTool(context=context),
        TaskGetTool(context=context),
        TaskUpdateTool(context=context),
        TaskListTool(context=context),
        TaskNextTool(context=context),
    ]


def get_task_tools_with_context(context: TaskToolContext):
    """
    Get all task tools bound to a specific context.
    
    This is the recommended way to get task tools for multi-agent scenarios.
    
    Args:
        context: The context containing task manager and agent ID
    
    Returns:
        List of task tool instances bound to the context
    
    Usage:
        from logicore.tasks import TaskToolContext, get_task_tools_with_context
        
        context = TaskToolContext(task_manager=manager, agent_id="agent-1")
        tools = get_task_tools_with_context(context)
        for tool in tools:
            agent.tool_executor.register_custom_tool(tool.name, tool.run)
    """
    return get_task_tools(context=context)


def get_task_tool_schemas():
    """Get schemas for all task tools."""
    return [tool.schema for tool in get_task_tools()]
