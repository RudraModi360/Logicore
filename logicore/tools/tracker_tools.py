"""
Tracker Tools: Agent tools for task tracking operations.

Provides tools for agents to create, update, and manage tasks
during execution. Adapted from gemini-cli's tracker tools.
"""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from logicore.tools.base import BaseTool, ToolResult
from logicore.runtime.tracker import (
    TrackerService,
    TrackerTask,
    TaskType,
    TaskStatus,
    TaskPriority,
)


# Global tracker instance (lazily initialized per project)
_tracker_instance: Optional[TrackerService] = None


def get_tracker(project_dir: Optional[str] = None) -> TrackerService:
    """Get or create tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = TrackerService(project_dir=project_dir)
    return _tracker_instance


def set_tracker(tracker: TrackerService) -> None:
    """Set the tracker instance (for dependency injection)."""
    global _tracker_instance
    _tracker_instance = tracker


# ============== Create Task Tool ==============

class TrackerCreateParams(BaseModel):
    title: str = Field(
        ...,
        description="Brief task title (< 100 chars)"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed task description"
    )
    type: Literal["epic", "task", "subtask", "bug"] = Field(
        "task",
        description="Task type: 'epic' (high-level), 'task' (standard), 'subtask' (granular), 'bug' (defect)"
    )
    parent_id: Optional[str] = Field(
        None,
        description="Parent task ID for hierarchy (6-char hex)"
    )
    priority: Literal["low", "medium", "high", "critical"] = Field(
        "medium",
        description="Task priority level"
    )
    dependencies: Optional[List[str]] = Field(
        None,
        description="List of task IDs this task depends on"
    )


class TrackerCreateTool(BaseTool):
    """Create a new task in the tracker."""
    
    name = "tracker_create"
    description = (
        "Create a new task for tracking progress. "
        "Use for: breaking down work into trackable items, "
        "planning multi-step implementations, organizing epics into tasks. "
        "Returns the created task with its ID."
    )
    args_schema = TrackerCreateParams
    
    def run(
        self,
        title: str,
        description: str = "",
        type: str = "task",
        parent_id: str = None,
        priority: str = "medium",
        dependencies: List[str] = None,
        **kwargs
    ) -> ToolResult:
        try:
            tracker = get_tracker()
            task = tracker.create_task(
                title=title,
                description=description or "",
                type=TaskType(type),
                priority=TaskPriority(priority),
                parent_id=parent_id,
                dependencies=dependencies,
            )
            return ToolResult(
                success=True,
                content=f"Created task [{task.id}]: {task.title}\nType: {task.type.value} | Priority: {task.priority.value}"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Update Task Tool ==============

class TrackerUpdateParams(BaseModel):
    task_id: str = Field(
        ...,
        description="Task ID to update (6-char hex)"
    )
    status: Optional[Literal["open", "in_progress", "blocked", "closed"]] = Field(
        None,
        description="New task status"
    )
    progress_percent: Optional[int] = Field(
        None,
        ge=0,
        le=100,
        description="Progress percentage (0-100)"
    )
    title: Optional[str] = Field(
        None,
        description="Updated task title"
    )
    note: Optional[str] = Field(
        None,
        description="Progress note to add"
    )


class TrackerUpdateTool(BaseTool):
    """Update an existing task's status or details."""
    
    name = "tracker_update"
    description = (
        "Update a task's status, progress, or details. "
        "Use for: marking tasks in progress, updating completion percentage, "
        "adding progress notes, changing task status."
    )
    args_schema = TrackerUpdateParams
    
    def run(
        self,
        task_id: str,
        status: str = None,
        progress_percent: int = None,
        title: str = None,
        note: str = None,
        **kwargs
    ) -> ToolResult:
        try:
            tracker = get_tracker()
            
            # Update task
            task = tracker.update_task(
                task_id=task_id,
                status=TaskStatus(status) if status else None,
                progress_percent=progress_percent,
                title=title,
            )
            
            # Add note if provided
            if note:
                tracker.add_note(task_id, note)
            
            return ToolResult(
                success=True,
                content=f"Updated task [{task.id}]: {task.title}\nStatus: {task.status.value} | Progress: {task.progress_percent}%"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== List Tasks Tool ==============

class TrackerListParams(BaseModel):
    status: Optional[Literal["open", "in_progress", "blocked", "closed", "all"]] = Field(
        None,
        description="Filter by status (default: all active tasks)"
    )
    type: Optional[Literal["epic", "task", "subtask", "bug"]] = Field(
        None,
        description="Filter by task type"
    )
    include_closed: bool = Field(
        False,
        description="Include closed/completed tasks"
    )


class TrackerListTool(BaseTool):
    """List all tasks with optional filtering."""
    
    name = "tracker_list"
    description = (
        "List all tasks in the tracker with optional filters. "
        "Use for: reviewing progress, checking what's open, "
        "finding blocked tasks, getting an overview."
    )
    args_schema = TrackerListParams
    
    def run(
        self,
        status: str = None,
        type: str = None,
        include_closed: bool = False,
        **kwargs
    ) -> ToolResult:
        try:
            tracker = get_tracker()
            
            # Handle "all" status
            filter_status = None
            if status and status != "all":
                filter_status = TaskStatus(status)
            
            tasks = tracker.list_tasks(
                status=filter_status,
                type=TaskType(type) if type else None,
                include_closed=include_closed or status == "all",
            )
            
            if not tasks:
                return ToolResult(success=True, content="No tasks found.")
            
            lines = [f"📋 Tasks ({len(tasks)} total)", "=" * 40]
            for task in tasks:
                lines.append(str(task))
            
            return ToolResult(success=True, content="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Get Task Tool ==============

class TrackerGetParams(BaseModel):
    task_id: str = Field(
        ...,
        description="Task ID to retrieve (6-char hex)"
    )


class TrackerGetTool(BaseTool):
    """Get details of a specific task."""
    
    name = "tracker_get"
    description = (
        "Get detailed information about a specific task. "
        "Use for: reviewing task details, checking dependencies, "
        "viewing progress notes."
    )
    args_schema = TrackerGetParams
    
    def run(self, task_id: str, **kwargs) -> ToolResult:
        try:
            tracker = get_tracker()
            task = tracker.get_task(task_id)
            
            lines = [
                f"📝 Task [{task.id}]: {task.title}",
                "=" * 40,
                f"Type: {task.type.value}",
                f"Status: {task.status.value}",
                f"Priority: {task.priority.value}",
                f"Progress: {task.progress_percent}%",
                f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M')}",
                f"Updated: {task.updated_at.strftime('%Y-%m-%d %H:%M')}",
            ]
            
            if task.description:
                lines.append(f"\nDescription:\n{task.description}")
            
            if task.parent_id:
                lines.append(f"\nParent: {task.parent_id}")
            
            if task.dependencies:
                lines.append(f"\nDependencies: {', '.join(task.dependencies)}")
            
            if task.notes:
                lines.append("\nNotes:")
                for note in task.notes[-5:]:  # Last 5 notes
                    lines.append(f"  - [{note.get('timestamp', '')}] {note.get('text', '')}")
            
            return ToolResult(success=True, content="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Add Dependency Tool ==============

class TrackerDependencyParams(BaseModel):
    task_id: str = Field(
        ...,
        description="Task that depends on another (6-char hex)"
    )
    depends_on_id: str = Field(
        ...,
        description="Task that must be completed first (6-char hex)"
    )


class TrackerAddDependencyTool(BaseTool):
    """Add a dependency between tasks."""
    
    name = "tracker_add_dependency"
    description = (
        "Add a dependency: task_id depends on depends_on_id. "
        "The dependent task cannot be closed until dependencies are closed. "
        "Validates against circular dependencies."
    )
    args_schema = TrackerDependencyParams
    
    def run(self, task_id: str, depends_on_id: str, **kwargs) -> ToolResult:
        try:
            tracker = get_tracker()
            task = tracker.add_dependency(task_id, depends_on_id)
            
            return ToolResult(
                success=True,
                content=f"Added dependency: [{task_id}] now depends on [{depends_on_id}]\nTask [{task.id}] dependencies: {task.dependencies}"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Visualize Tool ==============

class TrackerVisualizeParams(BaseModel):
    show_closed: bool = Field(
        False,
        description="Include closed tasks in visualization"
    )


class TrackerVisualizeTool(BaseTool):
    """Visualize the task tree."""
    
    name = "tracker_visualize"
    description = (
        "Show a visual tree of all tasks with hierarchy and status. "
        "Use for: getting an overview, presenting progress to user, "
        "understanding task relationships."
    )
    args_schema = TrackerVisualizeParams
    
    def run(self, show_closed: bool = False, **kwargs) -> ToolResult:
        try:
            tracker = get_tracker()
            visualization = tracker.visualize(show_closed=show_closed)
            return ToolResult(success=True, content=visualization)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Close Task Tool ==============

class TrackerCloseParams(BaseModel):
    task_id: str = Field(
        ...,
        description="Task ID to close (6-char hex)"
    )
    note: Optional[str] = Field(
        None,
        description="Completion note"
    )


class TrackerCloseTool(BaseTool):
    """Close/complete a task."""
    
    name = "tracker_close"
    description = (
        "Mark a task as completed/closed. "
        "Validates that all dependencies are closed first. "
        "Use when a task is fully done."
    )
    args_schema = TrackerCloseParams
    
    def run(self, task_id: str, note: str = None, **kwargs) -> ToolResult:
        try:
            tracker = get_tracker()
            task = tracker.close_task(task_id, note=note)
            
            return ToolResult(
                success=True,
                content=f"✅ Closed task [{task.id}]: {task.title}"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Tool Registration ==============

def get_tracker_tools() -> List[BaseTool]:
    """Get all tracker tools."""
    return [
        TrackerCreateTool(),
        TrackerUpdateTool(),
        TrackerListTool(),
        TrackerGetTool(),
        TrackerAddDependencyTool(),
        TrackerVisualizeTool(),
        TrackerCloseTool(),
    ]


def get_tracker_tool_schemas() -> List[Dict[str, Any]]:
    """Get schemas for all tracker tools."""
    return [tool.schema for tool in get_tracker_tools()]
