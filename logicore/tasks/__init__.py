"""
Task Management Module (V2)

Production-grade task tracking with:
- File-per-task storage for parallel reads
- DAG dependencies (blocks/blockedBy)
- Agent claiming for multi-agent support
- Activity tracking and summarization
- Locking infrastructure for concurrency control

Architecture:
- models.py: Task data model
- store.py: File-per-task storage with locking
- manager.py: Task lifecycle management
- tools.py: Agent tools (task_create, task_get, etc.)
- activity.py: Activity tracking and summarization

Usage:
    from logicore.tasks import TaskManager, TaskStore, ActivityTracker
    
    # Initialize
    store = TaskStore(base_dir="/project", task_list_id="session-123")
    manager = TaskManager(store)
    tracker = ActivityTracker()
    
    # Create and work on tasks
    task = manager.create_task("Implement login", active_form="Building login page")
    manager.claim_task(task.id, agent_id="agent-1")
    # ... do work ...
    manager.complete_task(task.id)
"""

from logicore.tasks.models import Task, TaskStatus
from logicore.tasks.store import TaskStore, TaskLock, NoOpLock, ThreadLock, FileLock
from logicore.tasks.manager import TaskManager
from logicore.tasks.activity import ActivityTracker, ToolActivity, ActivitySummary
from logicore.tasks.session_progress import SessionProgressWriter
from logicore.tasks.tools import (
    TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool, TaskNextTool,
    get_task_tools, get_task_tool_schemas, set_task_manager, get_task_manager,
    set_agent_id, get_agent_id,
)

__all__ = [
    # Models
    "Task",
    "TaskStatus",
    
    # Store
    "TaskStore",
    "TaskLock",
    "NoOpLock",
    "ThreadLock",
    "FileLock",
    
    # Manager
    "TaskManager",
    
    # Activity
    "ActivityTracker",
    "ToolActivity",
    "ActivitySummary",
    
    # Session Progress
    "SessionProgressWriter",
    
    # Tools
    "TaskCreateTool",
    "TaskGetTool",
    "TaskUpdateTool",
    "TaskListTool",
    "TaskNextTool",
    "get_task_tools",
    "get_task_tool_schemas",
    "set_task_manager",
    "get_task_manager",
    "set_agent_id",
    "get_agent_id",
]
