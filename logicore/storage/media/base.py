"""
Abstract media backend interface.

Media backend stores binary file bytes (attachments).
Metadata lives in the snapshot; actual bytes live here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path
from dataclasses import dataclass


@dataclass
class MediaInfo:
    """Metadata for a stored media file."""
    file_id: str
    path: str
    mime: str
    sha256: str
    size: int


class MediaBackend(ABC):
    """Abstract interface for binary media storage."""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the media storage (create directories if needed)."""

    @abstractmethod
    def put(
        self,
        session_id: str,
        file_id: str,
        data: bytes,
        mime: str = "application/octet-stream",
    ) -> MediaInfo:
        """Store a file and return its metadata."""

    @abstractmethod
    def get(self, path: str) -> Optional[bytes]:
        """Retrieve file contents by path."""

    @abstractmethod
    def delete(self, path: str) -> bool:
        """Delete a file by path."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists."""

    @abstractmethod
    def get_path(self, session_id: str, file_id: str) -> Path:
        """Get the full filesystem path for a file."""
