"""
Task data model for the V2 task management system.

Inspired by Claude Code's task architecture:
- Sequential integer IDs (human-readable)
- DAG dependencies (blocks/blockedBy)
- Owner/claiming for multi-agent support
- Active form for UI display during execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class TaskStatus(Enum):
    """Task lifecycle states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """
    Task data model with DAG dependencies.
    
    Each task represents a unit of work that can be:
    - Created by an agent
    - Claimed by an agent (ownership)
    - Blocked by other tasks (dependencies)
    - Tracked through its lifecycle
    
    Attributes:
        id: Sequential integer as string (e.g., "1", "2", "3")
        subject: Short title for the task
        description: Detailed description of what needs to be done
        active_form: Continuous verb form for UI (e.g., "Running tests")
        owner: Agent ID that claimed this task (None if unclaimed)
        status: Current lifecycle state
        blocks: List of task IDs this task blocks (forward dependencies)
        blocked_by: List of task IDs that block this task (reverse dependencies)
        metadata: Arbitrary key-value metadata
        created_at: When the task was created
        updated_at: When the task was last updated
        completed_at: When the task was completed (None if not completed)
    """
    
    id: str
    subject: str
    description: str = ""
    active_form: Optional[str] = None
    owner: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    blocks: List[str] = field(default_factory=list)
    blocked_by: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    @property
    def is_pending(self) -> bool:
        return self.status == TaskStatus.PENDING
    
    @property
    def is_in_progress(self) -> bool:
        return self.status == TaskStatus.IN_PROGRESS
    
    @property
    def is_completed(self) -> bool:
        return self.status == TaskStatus.COMPLETED
    
    @property
    def is_failed(self) -> bool:
        return self.status == TaskStatus.FAILED
    
    @property
    def is_blocked(self) -> bool:
        """Check if task has unresolved blockers."""
        return len(self.blocked_by) > 0
    
    @property
    def is_available(self) -> bool:
        """Check if task can be claimed (pending, no owner, not blocked)."""
        return (
            self.status == TaskStatus.PENDING
            and self.owner is None
            and len(self.blocked_by) == 0
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "active_form": self.active_form,
            "owner": self.owner,
            "status": self.status.value,
            "blocks": self.blocks,
            "blocked_by": self.blocked_by,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Task:
        """Create Task from dictionary."""
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data.get("description", ""),
            active_form=data.get("active_form"),
            owner=data.get("owner"),
            status=TaskStatus(data.get("status", "pending")),
            blocks=data.get("blocks", []),
            blocked_by=data.get("blocked_by", []),
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now()),
            updated_at=datetime.fromisoformat(data["updated_at"]) if isinstance(data.get("updated_at"), str) else data.get("updated_at", datetime.now()),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
        )
    
    def __str__(self) -> str:
        status_icons = {
            TaskStatus.PENDING: "[ ]",
            TaskStatus.IN_PROGRESS: "[>]",
            TaskStatus.COMPLETED: "[x]",
            TaskStatus.FAILED: "[!]",
        }
        icon = status_icons.get(self.status, "[?]")
        owner_str = f" (owner: {self.owner})" if self.owner else ""
        blocked_str = f" (blocked by: {', '.join(self.blocked_by)})" if self.blocked_by else ""
        return f"{icon} #{self.id} {self.subject}{owner_str}{blocked_str}"
