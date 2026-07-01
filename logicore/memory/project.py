"""
Project Memory - Removed.

This module has been stripped of all functionality.
Kept for import compatibility only.
"""

import threading
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional


class MemoryType(Enum):
    """Types of memories that can be stored."""
    APPROACH = "approach"
    LEARNING = "learning"
    KEY_STEP = "key_step"
    PATTERN = "pattern"
    PREFERENCE = "preference"
    DECISION = "decision"
    CONTEXT = "context"


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: Optional[int] = None
    memory_type: MemoryType = MemoryType.CONTEXT
    title: str = ""
    content: str = ""
    tags: List[str] = None
    project_id: Optional[str] = None
    created_at: datetime = None
    relevance_score: float = 1.0
    usage_count: int = 0

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.created_at is None:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.memory_type.value,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "project_id": self.project_id,
            "created_at": self.created_at.isoformat(),
            "relevance_score": self.relevance_score,
            "usage_count": self.usage_count,
        }


@dataclass
class ProjectContext:
    """Context about a project."""
    project_id: str = ""
    title: str = ""
    goal: str = ""
    environment: Dict[str, str] = None
    key_files: List[str] = None
    current_focus: Optional[str] = None
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.environment is None:
            self.environment = {}
        if self.key_files is None:
            self.key_files = []
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()


class ProjectMemory:
    """
    Stub for backward compatibility. All memory functionality has been removed.
    """

    def __init__(self, db_path: str = None):
        pass

    def create_project(self, project_id: str, title: str, goal: str = "",
                       environment: Dict[str, str] = None,
                       key_files: List[str] = None) -> Optional[ProjectContext]:
        return None

    def get_project(self, project_id: str) -> Optional[ProjectContext]:
        return None

    def update_project_focus(self, project_id: str, focus: str):
        pass

    def list_projects(self) -> List[ProjectContext]:
        return []

    def add_memory(self, memory_type: MemoryType, title: str, content: str,
                   tags: List[str] = None, project_id: str = None) -> Optional[MemoryEntry]:
        return None

    def search_memories(self, query: str, project_id: str = None,
                        memory_type: MemoryType = None, limit: int = 10) -> List[MemoryEntry]:
        return []

    def get_memories(self, project_id: str = None, memory_type: MemoryType = None,
                     limit: int = 50) -> List[MemoryEntry]:
        return []

    def update_memory_relevance(self, memory_id: int, score: float):
        pass

    def delete_memory(self, memory_id: int):
        pass

    def export_for_llm(self, project_id: str = None, format: str = "markdown",
                       include_global: bool = True) -> str:
        return ""

    def export_project_context(self, project_id: str) -> str:
        return ""


_project_memory: Optional[ProjectMemory] = None
_project_memory_lock = threading.Lock()


def get_project_memory() -> ProjectMemory:
    """Get or create the global ProjectMemory instance (stub)."""
    global _project_memory
    if _project_memory is None:
        with _project_memory_lock:
            if _project_memory is None:
                _project_memory = ProjectMemory()
    return _project_memory
