"""
Abstract database backend interface.

All database backends (SQLite, PostgreSQL, etc.) implement this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime


class DatabaseBackend(ABC):
    """Abstract interface for session database persistence."""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the database (create tables if needed)."""

    @abstractmethod
    def close(self) -> None:
        """Close the database connection."""

    @abstractmethod
    def create_session(
        self,
        session_id: str,
        provider: str = "",
        model: str = "",
        description: str = "",
    ) -> None:
        """Create a new session record."""

    @abstractmethod
    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""

    @abstractmethod
    def save_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Save session messages (JSON)."""

    @abstractmethod
    def load_messages(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """Load session messages."""

    @abstractmethod
    def update_session(
        self,
        session_id: str,
        description: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        snapshot_path: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        """Update session metadata fields."""

    @abstractmethod
    def increment_revision(self, session_id: str) -> int:
        """Increment and return the new revision number."""

    @abstractmethod
    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List sessions ordered by last updated."""

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its telemetry."""

    @abstractmethod
    def save_telemetry(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        tool_calls: int = 0,
        api_calls: int = 0,
    ) -> None:
        """Save or update telemetry counters for a session."""

    @abstractmethod
    def load_telemetry(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load telemetry data for a session."""

    @abstractmethod
    def get_schema_version(self) -> int:
        """Get the current schema version."""

    @abstractmethod
    def set_schema_version(self, version: int) -> None:
        """Set the schema version."""

    @abstractmethod
    def add_pending_sync(self, session_id: str) -> None:
        """Mark a session as needing snapshot sync (for crash recovery)."""

    @abstractmethod
    def remove_pending_sync(self, session_id: str) -> None:
        """Clear a session from pending syncs (after snapshot written)."""

    @abstractmethod
    def get_pending_syncs(self) -> List[str]:
        """Get all session IDs that need snapshot sync (for crash recovery)."""

    def reload_schema(self) -> None:
        """Notify external tools (e.g. Supabase PostgREST) to reload schema cache.

        Override in subclasses that need it (PostgreSQL/Supabase).
        Default is a no-op for backends where it's not needed (SQLite).
        """
