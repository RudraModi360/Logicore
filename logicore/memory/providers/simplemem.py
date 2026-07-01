"""
SimpleMem - Removed.

This module has been stripped of all functionality.
Kept for import compatibility only.
"""


class EmbeddingModel:
    """Placeholder for backward compatibility."""
    def __init__(self, *args, **kwargs):
        pass


class VectorStore:
    """Placeholder for backward compatibility."""
    def __init__(self, *args, **kwargs):
        pass


class Dialogue:
    """Placeholder for backward compatibility."""
    def __init__(self, *args, **kwargs):
        pass


class AgentrySimpleMem:
    """Placeholder for backward compatibility."""

    def __init__(self, *args, **kwargs):
        self.user_id = kwargs.get("user_id", "default")
        self.session_id = kwargs.get("session_id", "default")

    async def on_user_message(self, *args, **kwargs):
        return ""

    async def on_assistant_message(self, *args, **kwargs):
        pass

    async def process_pending(self):
        pass

    def get_stats(self):
        return {"status": "disabled"}

    def clear_memories(self):
        pass
