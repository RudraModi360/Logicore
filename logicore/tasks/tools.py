"""
Agent tools for task management.

Tools:
- task_create: Create a new task
- task_get: Get task details and claim it
- task_update: Update task status/fields
- task_list: List tasks with filtering
- task_next: Get next available task

These tools wrap TaskManager and are registered in the tool registry.
"""

from typing import Optional, List
from pydantic import BaseModel, Field
from logicore.tools.base import BaseTool, ToolResult


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

_task_manager = None


def set_task_manager(manager):
    """Set the task manager instance for tools to use."""
    global _task_manager
    _task_manager = manager


def get_task_manager():
    """Get the current task manager instance."""
    return _task_manager


_agent_id = None


def set_agent_id(agent_id: str):
    """Set the agent ID for task claiming (multi-agent coordination)."""
    global _agent_id
    _agent_id = agent_id


def get_agent_id() -> str:
    """Get the current agent ID."""
    return _agent_id or "agent"


class TaskCreateTool(BaseTool):
    """Create a new task in the task list."""
    name = "task_create"
    description = (
        "Create a new task to track work. Tasks can have dependencies "
        "(blocked_by) that prevent claiming until resolved. Use active_form "
        "to describe what will be shown in the UI while this task runs."
    )
    args_schema = TaskCreateArgs
    
    def run(self, subject: str, description: str = "", active_form: Optional[str] = None,
            blocked_by: Optional[List[str]] = None) -> ToolResult:
        if not _task_manager:
            return ToolResult(success=False, error="Task manager not initialized")
        try:
            task = _task_manager.create_task(
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
            return ToolResult(success=False, error=str(e))


class TaskGetTool(BaseTool):
    """Get task details and optionally claim it."""
    name = "task_get"
    description = (
        "Get details of a task by ID. Optionally claim it (assign ownership). "
        "Claiming validates the task is available (not blocked, not already claimed)."
    )
    args_schema = TaskGetArgs
    
    def run(self, task_id: str, claim: bool = False) -> ToolResult:
        if not _task_manager:
            return ToolResult(success=False, error="Task manager not initialized")
        try:
            if claim:
                task = _task_manager.claim_task(task_id, agent_id=get_agent_id(), check_agent_busy=False)
                return ToolResult(
                    success=True,
                    content={**task.to_dict(), "claimed": True}
                )
            else:
                task = _task_manager.store.get(task_id)
                if not task:
                    return ToolResult(success=False, error=f"Task #{task_id} not found")
                return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class TaskUpdateTool(BaseTool):
    """Update task status and fields."""
    name = "task_update"
    description = (
        "Update a task's status, active form, or other fields. "
        "Setting status to 'completed' will cascade-unblock dependent tasks."
    )
    args_schema = TaskUpdateArgs
    
    def run(self, task_id: str, status: Optional[str] = None,
            active_form: Optional[str] = None, subject: Optional[str] = None,
            description: Optional[str] = None) -> ToolResult:
        if not _task_manager:
            return ToolResult(success=False, error="Task manager not initialized")
        try:
            if status == "completed":
                task = _task_manager.complete_task(task_id)
            elif status == "failed":
                task = _task_manager.fail_task(task_id)
            else:
                task = _task_manager.update_task(
                    task_id,
                    status=status,
                    active_form=active_form,
                    subject=subject,
                    description=description,
                )
            return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class TaskListTool(BaseTool):
    """List tasks with filtering."""
    name = "task_list"
    description = (
        "List tasks in the task list. Can filter by status or show only "
        "available tasks (pending, unclaimed, unblocked)."
    )
    args_schema = TaskListArgs
    
    def run(self, status: Optional[str] = None, available_only: bool = False) -> ToolResult:
        if not _task_manager:
            return ToolResult(success=False, error="Task manager not initialized")
        try:
            if available_only:
                tasks = _task_manager.store.list_available()
            elif status:
                tasks = _task_manager.store.list_by_status(status)
            else:
                tasks = _task_manager.store.list_all()
            
            summary = _task_manager.get_task_summary()
            return ToolResult(
                success=True,
                content={
                    "tasks": [t.to_dict() for t in tasks],
                    "summary": summary,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class TaskNextTool(BaseTool):
    """Get the next available task."""
    name = "task_next"
    description = (
        "Get the next available task to work on. Returns the first task "
        "that is pending, unclaimed, and unblocked."
    )
    args_schema = TaskNextArgs
    
    def run(self) -> ToolResult:
        if not _task_manager:
            return ToolResult(success=False, error="Task manager not initialized")
        try:
            task = _task_manager.get_next_task()
            if not task:
                return ToolResult(
                    success=True,
                    content={"message": "No available tasks", "task": None}
                )
            return ToolResult(success=True, content=task.to_dict())
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def get_task_tools():
    """Get all task tools."""
    return [
        TaskCreateTool(),
        TaskGetTool(),
        TaskUpdateTool(),
        TaskListTool(),
        TaskNextTool(),
    ]


def get_task_tool_schemas():
    """Get schemas for all task tools."""
    return [tool.schema for tool in get_task_tools()]
