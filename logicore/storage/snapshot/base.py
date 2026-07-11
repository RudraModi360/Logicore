"""
Abstract snapshot backend interface.

Snapshots are async, stateless JSON manifests of session state.
The worker reads from SQL, serializes, and overwrites the snapshot file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pathlib import Path


class SnapshotBackend(ABC):
    """Abstract interface for session snapshot persistence."""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the snapshot storage (create directories if needed)."""

    @abstractmethod
    def save_snapshot(self, session_id: str, manifest: Dict[str, Any]) -> None:
        """Save (overwrite) a session snapshot manifest."""

    @abstractmethod
    def load_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session snapshot manifest."""

    @abstractmethod
    def delete_snapshot(self, session_id: str) -> bool:
        """Delete a session snapshot."""

    @abstractmethod
    def snapshot_exists(self, session_id: str) -> bool:
        """Check if a snapshot exists."""

    @abstractmethod
    def get_snapshot_path(self, session_id: str) -> Path:
        """Get the file path for a session snapshot."""
