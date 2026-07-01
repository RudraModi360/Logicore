"""
TrackerService: CRUD operations for task tracking with persistence.

Provides:
- Create, read, update, delete tasks
- Hierarchical task management (parent/child)
- Dependency management with circular dependency detection
- Persistence to JSON file
- Task tree visualization

Adapted from gemini-cli's trackerService.ts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from logicore.runtime.tracker.types import (
    TaskType,
    TaskStatus,
    TaskPriority,
    TrackerTask,
    generate_task_id,
)


class CircularDependencyError(Exception):
    """Raised when a circular dependency is detected."""
    pass


class TaskNotFoundError(Exception):
    """Raised when a task is not found."""
    pass


class DependencyNotClosedError(Exception):
    """Raised when trying to close a task with open dependencies."""
    pass


class TrackerService:
    """
    Task tracking service with hierarchical support and persistence.
    
    Features:
    - CRUD operations for tasks
    - Parent-child task relationships
    - Dependency management with circular detection
    - JSON file persistence
    - Task tree visualization
    
    Usage:
        tracker = TrackerService(project_dir="/path/to/project")
        
        # Create tasks
        epic = tracker.create_task("Build auth system", type=TaskType.EPIC)
        task = tracker.create_task("Implement login", parent_id=epic.id)
        
        # Update status
        tracker.update_task(task.id, status=TaskStatus.IN_PROGRESS)
        
        # Add dependencies
        task2 = tracker.create_task("Setup database")
        tracker.add_dependency(task.id, task2.id)  # task depends on task2
        
        # Visualize
        print(tracker.visualize())
    """
    
    def __init__(
        self,
        project_dir: Optional[str] = None,
        auto_save: bool = True,
    ):
        """
        Initialize tracker service.
        
        Args:
            project_dir: Project directory for persistence (default: cwd)
            auto_save: Automatically save after each modification
        """
        self.project_dir = Path(project_dir or os.getcwd())
        self.tracker_dir = self.project_dir / ".logicore" / "tracker"
        self.tasks_file = self.tracker_dir / "tasks.json"
        self.auto_save = auto_save
        
        self._tasks: Dict[str, TrackerTask] = {}
        self._load()
    
    def _ensure_dir(self) -> None:
        """Ensure tracker directory exists."""
        self.tracker_dir.mkdir(parents=True, exist_ok=True)
    
    def _load(self) -> None:
        """Load tasks from persistent storage."""
        if self.tasks_file.exists():
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._tasks = {
                    task_id: TrackerTask.from_dict(task_data)
                    for task_id, task_data in data.get("tasks", {}).items()
                }
            except (json.JSONDecodeError, KeyError) as e:
                # Corrupted file, start fresh
                self._tasks = {}
    
    def _save(self) -> None:
        """Save tasks to persistent storage."""
        self._ensure_dir()
        data = {
            "version": "1.0",
            "updated_at": datetime.now().isoformat(),
            "tasks": {
                task_id: task.to_dict()
                for task_id, task in self._tasks.items()
            }
        }
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    
    def _maybe_save(self) -> None:
        """Save if auto_save is enabled."""
        if self.auto_save:
            self._save()
    
    # ========== CRUD Operations ==========
    
    def create_task(
        self,
        title: str,
        description: str = "",
        type: TaskType = TaskType.TASK,
        status: TaskStatus = TaskStatus.OPEN,
        priority: TaskPriority = TaskPriority.MEDIUM,
        parent_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrackerTask:
        """
        Create a new task.
        
        Args:
            title: Task title
            description: Detailed description
            type: Task type (EPIC, TASK, SUBTASK, BUG)
            status: Initial status (default: OPEN)
            priority: Priority level
            parent_id: Parent task ID for hierarchy
            dependencies: List of task IDs this depends on
            metadata: Arbitrary metadata
            
        Returns:
            Created TrackerTask
            
        Raises:
            TaskNotFoundError: If parent_id references non-existent task
            CircularDependencyError: If dependencies would create a cycle
        """
        # Validate parent exists
        if parent_id and parent_id not in self._tasks:
            raise TaskNotFoundError(f"Parent task '{parent_id}' not found")
        
        # Validate dependencies exist
        deps = dependencies or []
        for dep_id in deps:
            if dep_id not in self._tasks:
                raise TaskNotFoundError(f"Dependency task '{dep_id}' not found")
        
        task = TrackerTask(
            id=generate_task_id(),
            title=title,
            description=description,
            type=type,
            status=status,
            priority=priority,
            parent_id=parent_id,
            dependencies=deps,
            metadata=metadata or {},
        )
        
        # Check for circular dependencies
        if deps:
            self._check_circular_dependency(task.id, deps)
        
        self._tasks[task.id] = task
        
        # Mark parent as having children
        if parent_id:
            self._tasks[parent_id].metadata["_has_children"] = True
        
        self._maybe_save()
        return task
    
    def get_task(self, task_id: str) -> TrackerTask:
        """
        Get a task by ID.
        
        Args:
            task_id: Task identifier
            
        Returns:
            TrackerTask
            
        Raises:
            TaskNotFoundError: If task doesn't exist
        """
        if task_id not in self._tasks:
            raise TaskNotFoundError(f"Task '{task_id}' not found")
        return self._tasks[task_id]
    
    def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        progress_percent: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrackerTask:
        """
        Update an existing task.
        
        Args:
            task_id: Task to update
            title: New title (optional)
            description: New description (optional)
            status: New status (optional)
            priority: New priority (optional)
            progress_percent: Progress percentage (optional)
            metadata: Metadata to merge (optional)
            
        Returns:
            Updated TrackerTask
            
        Raises:
            TaskNotFoundError: If task doesn't exist
            DependencyNotClosedError: If trying to close with open deps
        """
        task = self.get_task(task_id)
        
        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority
        if progress_percent is not None:
            task.progress_percent = max(0, min(100, progress_percent))
        if metadata is not None:
            task.metadata.update(metadata)
        
        # Handle status change
        if status is not None and status != task.status:
            if status == TaskStatus.CLOSED:
                self._validate_can_close(task_id)
                task.closed_at = datetime.now()
            task.status = status
        
        task.updated_at = datetime.now()
        self._maybe_save()
        return task
    
    def delete_task(self, task_id: str, cascade: bool = False) -> None:
        """
        Delete a task.
        
        Args:
            task_id: Task to delete
            cascade: Also delete child tasks
            
        Raises:
            TaskNotFoundError: If task doesn't exist
            ValueError: If task has children and cascade=False
        """
        task = self.get_task(task_id)
        
        # Check for children
        children = self.get_children(task_id)
        if children and not cascade:
            raise ValueError(
                f"Task '{task_id}' has {len(children)} children. "
                "Use cascade=True to delete them too."
            )
        
        # Delete children first
        if cascade:
            for child in children:
                self.delete_task(child.id, cascade=True)
        
        # Remove from dependencies of other tasks
        for other_task in self._tasks.values():
            if task_id in other_task.dependencies:
                other_task.dependencies.remove(task_id)
        
        del self._tasks[task_id]
        self._maybe_save()
    
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        type: Optional[TaskType] = None,
        parent_id: Optional[str] = None,
        include_closed: bool = True,
    ) -> List[TrackerTask]:
        """
        List tasks with optional filtering.
        
        Args:
            status: Filter by status
            type: Filter by type
            parent_id: Filter by parent (None = root tasks only)
            include_closed: Include closed tasks
            
        Returns:
            List of matching tasks
        """
        tasks = list(self._tasks.values())
        
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if type is not None:
            tasks = [t for t in tasks if t.type == type]
        if parent_id is not None:
            tasks = [t for t in tasks if t.parent_id == parent_id]
        elif parent_id is None:
            # If parent_id not specified, return all tasks
            pass
        if not include_closed:
            tasks = [t for t in tasks if t.status != TaskStatus.CLOSED]
        
        # Sort by priority, then created_at
        priority_order = {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3,
        }
        tasks.sort(key=lambda t: (priority_order.get(t.priority, 2), t.created_at))
        return tasks
    
    def get_root_tasks(self) -> List[TrackerTask]:
        """Get all top-level tasks (no parent)."""
        return [t for t in self._tasks.values() if t.parent_id is None]
    
    def get_children(self, task_id: str) -> List[TrackerTask]:
        """Get direct children of a task."""
        return [t for t in self._tasks.values() if t.parent_id == task_id]
    
    def get_all_descendants(self, task_id: str) -> List[TrackerTask]:
        """Get all descendants (children, grandchildren, etc.)."""
        descendants = []
        for child in self.get_children(task_id):
            descendants.append(child)
            descendants.extend(self.get_all_descendants(child.id))
        return descendants
    
    # ========== Dependency Management ==========
    
    def add_dependency(self, task_id: str, depends_on_id: str) -> TrackerTask:
        """
        Add a dependency to a task.
        
        Args:
            task_id: Task that depends on another
            depends_on_id: Task that must be completed first
            
        Returns:
            Updated task
            
        Raises:
            TaskNotFoundError: If either task doesn't exist
            CircularDependencyError: If this would create a cycle
        """
        task = self.get_task(task_id)
        _ = self.get_task(depends_on_id)  # Validate exists
        
        if depends_on_id in task.dependencies:
            return task  # Already exists
        
        # Check for circular dependency
        self._check_circular_dependency(task_id, [depends_on_id])
        
        task.dependencies.append(depends_on_id)
        task.updated_at = datetime.now()
        self._maybe_save()
        return task
    
    def remove_dependency(self, task_id: str, depends_on_id: str) -> TrackerTask:
        """Remove a dependency from a task."""
        task = self.get_task(task_id)
        if depends_on_id in task.dependencies:
            task.dependencies.remove(depends_on_id)
            task.updated_at = datetime.now()
            self._maybe_save()
        return task
    
    def get_dependencies(self, task_id: str) -> List[TrackerTask]:
        """Get all tasks that a task depends on."""
        task = self.get_task(task_id)
        return [self._tasks[dep_id] for dep_id in task.dependencies if dep_id in self._tasks]
    
    def get_dependents(self, task_id: str) -> List[TrackerTask]:
        """Get all tasks that depend on a task."""
        return [t for t in self._tasks.values() if task_id in t.dependencies]
    
    def _check_circular_dependency(self, task_id: str, new_deps: List[str]) -> None:
        """
        Check if adding dependencies would create a cycle.
        
        Raises:
            CircularDependencyError: If cycle detected
        """
        visited = set()
        
        def dfs(current_id: str, path: List[str]) -> None:
            if current_id in path:
                cycle = " → ".join(path + [current_id])
                raise CircularDependencyError(f"Circular dependency detected: {cycle}")
            
            if current_id in visited:
                return
            visited.add(current_id)
            
            # Get dependencies of current task
            deps = []
            if current_id in self._tasks:
                deps = self._tasks[current_id].dependencies
            elif current_id == task_id:
                deps = new_deps
            
            for dep_id in deps:
                dfs(dep_id, path + [current_id])
        
        # Start DFS from each new dependency
        for dep_id in new_deps:
            dfs(dep_id, [task_id])
    
    def _validate_can_close(self, task_id: str) -> None:
        """
        Validate that a task can be closed.
        
        Raises:
            DependencyNotClosedError: If dependencies are not closed
        """
        task = self.get_task(task_id)
        open_deps = [
            self._tasks[dep_id]
            for dep_id in task.dependencies
            if dep_id in self._tasks and self._tasks[dep_id].status != TaskStatus.CLOSED
        ]
        
        if open_deps:
            dep_names = ", ".join(f"[{d.id}] {d.title}" for d in open_deps)
            raise DependencyNotClosedError(
                f"Cannot close task '{task_id}': open dependencies: {dep_names}"
            )
    
    # ========== Task Operations ==========
    
    def close_task(self, task_id: str, note: Optional[str] = None) -> TrackerTask:
        """
        Close a task (mark as completed).
        
        Args:
            task_id: Task to close
            note: Optional completion note
            
        Returns:
            Updated task
            
        Raises:
            DependencyNotClosedError: If dependencies aren't closed
        """
        self._validate_can_close(task_id)
        task = self.get_task(task_id)
        
        if note:
            task.add_note(f"Completed: {note}")
        
        task.status = TaskStatus.CLOSED
        task.progress_percent = 100
        task.closed_at = datetime.now()
        task.updated_at = datetime.now()
        
        self._maybe_save()
        return task
    
    def start_task(self, task_id: str) -> TrackerTask:
        """Mark a task as in progress."""
        return self.update_task(task_id, status=TaskStatus.IN_PROGRESS)
    
    def block_task(self, task_id: str, reason: str) -> TrackerTask:
        """Mark a task as blocked with a reason."""
        task = self.update_task(task_id, status=TaskStatus.BLOCKED)
        task.add_note(f"Blocked: {reason}")
        self._maybe_save()
        return task
    
    def add_note(self, task_id: str, note: str, author: str = "agent") -> TrackerTask:
        """Add a note to a task."""
        task = self.get_task(task_id)
        task.add_note(note, author)
        self._maybe_save()
        return task
    
    # ========== Visualization ==========
    
    def visualize(self, show_closed: bool = False) -> str:
        """
        Generate a text visualization of the task tree.
        
        Args:
            show_closed: Include closed tasks
            
        Returns:
            Formatted string representation
        """
        lines = ["📋 Task Tracker", "=" * 40]
        
        # Get stats
        total = len(self._tasks)
        open_count = sum(1 for t in self._tasks.values() if t.status == TaskStatus.OPEN)
        in_progress = sum(1 for t in self._tasks.values() if t.status == TaskStatus.IN_PROGRESS)
        blocked = sum(1 for t in self._tasks.values() if t.status == TaskStatus.BLOCKED)
        closed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.CLOSED)
        
        lines.append(f"Total: {total} | Open: {open_count} | In Progress: {in_progress} | Blocked: {blocked} | Closed: {closed}")
        lines.append("")
        
        # Render tree
        root_tasks = self.get_root_tasks()
        for task in root_tasks:
            if not show_closed and task.status == TaskStatus.CLOSED:
                continue
            self._render_task(task, lines, "", show_closed)
        
        if not root_tasks:
            lines.append("(no tasks)")
        
        return "\n".join(lines)
    
    def _render_task(
        self,
        task: TrackerTask,
        lines: List[str],
        prefix: str,
        show_closed: bool,
    ) -> None:
        """Recursively render a task and its children."""
        lines.append(f"{prefix}{task}")
        
        # Show dependencies
        if task.dependencies:
            dep_ids = ", ".join(task.dependencies)
            lines.append(f"{prefix}  └─ depends on: {dep_ids}")
        
        # Render children
        children = self.get_children(task.id)
        for i, child in enumerate(children):
            if not show_closed and child.status == TaskStatus.CLOSED:
                continue
            is_last = i == len(children) - 1
            child_prefix = prefix + ("    " if is_last else "│   ")
            lines.append(f"{prefix}{'└── ' if is_last else '├── '}{child}")
            
            # Recurse for grandchildren
            grandchildren = self.get_children(child.id)
            for gc in grandchildren:
                if not show_closed and gc.status == TaskStatus.CLOSED:
                    continue
                self._render_task(gc, lines, child_prefix, show_closed)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics for telemetry."""
        return {
            "total_tasks": len(self._tasks),
            "by_status": {
                status.value: sum(1 for t in self._tasks.values() if t.status == status)
                for status in TaskStatus
            },
            "by_type": {
                type.value: sum(1 for t in self._tasks.values() if t.type == type)
                for type in TaskType
            },
            "avg_progress": (
                sum(t.progress_percent for t in self._tasks.values()) / len(self._tasks)
                if self._tasks else 0
            ),
        }
    
    def clear_all(self) -> None:
        """Clear all tasks (use with caution)."""
        self._tasks.clear()
        self._maybe_save()
