"""
Agentry Memory Module

Memory backends have been removed. This module is kept for backward compatibility
but all memory functionality is disabled.
"""


class MemoryType:
    """Placeholder for backward compatibility."""
    APPROACH = "approach"
    LEARNING = "learning"
    KEY_STEP = "key_step"
    PATTERN = "pattern"
    PREFERENCE = "preference"
    DECISION = "decision"
    CONTEXT = "context"


class MemoryEntry:
    """Placeholder for backward compatibility."""
    def __init__(self, *args, **kwargs):
        pass


class ProjectContext:
    """Placeholder for backward compatibility."""
    def __init__(self, *args, **kwargs):
        pass


class ProjectMemory:
    """Placeholder for backward compatibility."""

    def __init__(self, db_path=None):
        pass

    def create_project(self, *args, **kwargs):
        return None

    def get_project(self, *args, **kwargs):
        return None

    def update_project_focus(self, *args, **kwargs):
        pass

    def list_projects(self):
        return []

    def add_memory(self, *args, **kwargs):
        return None

    def search_memories(self, *args, **kwargs):
        return []

    def get_memories(self, *args, **kwargs):
        return []

    def update_memory_relevance(self, *args, **kwargs):
        pass

    def delete_memory(self, *args, **kwargs):
        pass

    def export_for_llm(self, *args, **kwargs):
        return ""

    def export_project_context(self, *args, **kwargs):
        return ""


def get_project_memory():
    """Get or create the global ProjectMemory instance (stub)."""
    return ProjectMemory()


class AgentrySimpleMem:
    """Placeholder for backward compatibility."""

    def __init__(self, *args, **kwargs):
        pass

    async def on_user_message(self, *args, **kwargs):
        return ""

    async def on_assistant_message(self, *args, **kwargs):
        pass

    async def process_pending(self):
        pass

    def get_stats(self):
        return {}

    def clear_memories(self):
        pass


__all__ = [
    "ProjectMemory",
    "ProjectContext",
    "MemoryType",
    "MemoryEntry",
    "get_project_memory",
    "AgentrySimpleMem",
]
