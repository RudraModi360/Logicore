"""
Task Tracker Module

Provides hierarchical task tracking with dependencies, status management,
and progress visualization. Inspired by gemini-cli's trackerService.

Components:
- TaskType: Task classification (EPIC, TASK, SUBTASK, BUG)
- TaskStatus: Task state (OPEN, IN_PROGRESS, BLOCKED, CLOSED)
- TrackerTask: Task data model
- TrackerService: CRUD operations with persistence

Usage:
    from logicore.runtime.tracker import TrackerService, TaskType, TaskStatus
    
    tracker = TrackerService()
    
    # Create hierarchical tasks
    epic = tracker.create_task("Build feature X", type=TaskType.EPIC)
    task = tracker.create_task("Implement API", parent_id=epic.id)
    
    # Track progress
    tracker.update_task(task.id, status=TaskStatus.IN_PROGRESS)
    
    # Visualize
    print(tracker.visualize())
"""

from logicore.runtime.tracker.types import (
    TaskType,
    TaskStatus,
    TaskPriority,
    TrackerTask,
)
from logicore.runtime.tracker.service import TrackerService

__all__ = [
    "TaskType",
    "TaskStatus",
    "TaskPriority",
    "TrackerTask",
    "TrackerService",
]
