"""
TrackerTypes: Data models for the task tracking system.

Adapted from gemini-cli's trackerTypes.ts with Python dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class TaskType(Enum):
    """
    Task classification types.
    
    Hierarchy: EPIC > TASK > SUBTASK
    BUG is a special type that can exist at any level.
    """
    EPIC = "epic"         # High-level goal or feature
    TASK = "task"         # Standard work item
    SUBTASK = "subtask"   # Granular step within a task
    BUG = "bug"           # Defect or issue to fix


class TaskStatus(Enum):
    """
    Task lifecycle states.
    
    Transitions:
    - OPEN → IN_PROGRESS → CLOSED
    - OPEN → BLOCKED (waiting on dependencies)
    - BLOCKED → IN_PROGRESS (dependencies resolved)
    - IN_PROGRESS → BLOCKED (new blocker found)
    """
    OPEN = "open"              # Not started
    IN_PROGRESS = "in_progress"  # Currently being worked on
    BLOCKED = "blocked"        # Waiting on dependencies/external factors
    CLOSED = "closed"          # Completed


class TaskPriority(Enum):
    """Task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def generate_task_id() -> str:
    """Generate a 6-character hex task ID."""
    return uuid.uuid4().hex[:6]


@dataclass
class TrackerTask:
    """
    Task data model with hierarchical support.
    
    Attributes:
        id: Unique 6-char hex identifier
        title: Brief task title (< 100 chars)
        description: Detailed task description
        type: Task classification (EPIC, TASK, SUBTASK, BUG)
        status: Current lifecycle state
        priority: Task priority level
        parent_id: ID of parent task (for hierarchy)
        dependencies: List of task IDs this task depends on
        created_at: Creation timestamp
        updated_at: Last update timestamp
        closed_at: Completion timestamp (when status → CLOSED)
        metadata: Arbitrary key-value metadata
        subagent_session_id: Session ID if delegated to subagent
        progress_percent: Manual progress indicator (0-100)
        notes: List of progress notes/updates
    """
    
    id: str
    title: str
    description: str = ""
    type: TaskType = TaskType.TASK
    status: TaskStatus = TaskStatus.OPEN
    priority: TaskPriority = TaskPriority.MEDIUM
    parent_id: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    subagent_session_id: Optional[str] = None
    progress_percent: int = 0
    notes: List[Dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        """Ensure id is generated if not provided."""
        if not self.id:
            self.id = generate_task_id()
    
    @property
    def is_completed(self) -> bool:
        """Check if task is completed."""
        return self.status == TaskStatus.CLOSED
    
    @property
    def is_blocked(self) -> bool:
        """Check if task is blocked."""
        return self.status == TaskStatus.BLOCKED
    
    @property
    def is_active(self) -> bool:
        """Check if task is in progress."""
        return self.status == TaskStatus.IN_PROGRESS
    
    @property
    def has_children(self) -> bool:
        """Check if task has subtasks (set by TrackerService)."""
        return self.metadata.get("_has_children", False)
    
    def add_note(self, note: str, author: str = "agent") -> None:
        """Add a progress note to the task."""
        self.notes.append({
            "text": note,
            "author": author,
            "timestamp": datetime.now().isoformat(),
        })
        self.updated_at = datetime.now()
    
    def update_progress(self, percent: int, note: Optional[str] = None) -> None:
        """Update progress percentage with optional note."""
        self.progress_percent = max(0, min(100, percent))
        if note:
            self.add_note(f"Progress {percent}%: {note}")
        self.updated_at = datetime.now()
        
        # Auto-close if 100%
        if self.progress_percent == 100 and self.status != TaskStatus.CLOSED:
            self.status = TaskStatus.CLOSED
            self.closed_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "type": self.type.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "parent_id": self.parent_id,
            "dependencies": self.dependencies,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "metadata": self.metadata,
            "subagent_session_id": self.subagent_session_id,
            "progress_percent": self.progress_percent,
            "notes": self.notes,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrackerTask":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            type=TaskType(data.get("type", "task")),
            status=TaskStatus(data.get("status", "open")),
            priority=TaskPriority(data.get("priority", "medium")),
            parent_id=data.get("parent_id"),
            dependencies=data.get("dependencies", []),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now()),
            updated_at=datetime.fromisoformat(data["updated_at"]) if isinstance(data.get("updated_at"), str) else data.get("updated_at", datetime.now()),
            closed_at=datetime.fromisoformat(data["closed_at"]) if data.get("closed_at") else None,
            metadata=data.get("metadata", {}),
            subagent_session_id=data.get("subagent_session_id"),
            progress_percent=data.get("progress_percent", 0),
            notes=data.get("notes", []),
        )
    
    def __str__(self) -> str:
        """String representation for display."""
        status_icons = {
            TaskStatus.OPEN: "○",
            TaskStatus.IN_PROGRESS: "◐",
            TaskStatus.BLOCKED: "⊗",
            TaskStatus.CLOSED: "●",
        }
        type_icons = {
            TaskType.EPIC: "📋",
            TaskType.TASK: "📝",
            TaskType.SUBTASK: "  •",
            TaskType.BUG: "🐛",
        }
        icon = status_icons.get(self.status, "?")
        type_icon = type_icons.get(self.type, "")
        progress = f" [{self.progress_percent}%]" if self.progress_percent > 0 else ""
        return f"{icon} {type_icon} [{self.id}] {self.title}{progress}"
