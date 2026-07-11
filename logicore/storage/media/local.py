"""
Local filesystem media backend.

Stores binary file bytes at: ~/.logicore/assets/{session_id}/{file_id}.{ext}
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .base import MediaBackend, MediaInfo


def _guess_extension(mime: str) -> str:
    """Map MIME type to file extension."""
    mime_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/json": ".json",
        "text/markdown": ".md",
        "text/html": ".html",
        "text/css": ".css",
        "text/javascript": ".js",
        "application/zip": ".zip",
        "application/octet-stream": ".bin",
    }
    return mime_map.get(mime, ".bin")


def _sha256(data: bytes) -> str:
    """Compute SHA256 hash of data."""
    return hashlib.sha256(data).hexdigest()


class LocalMediaBackend(MediaBackend):
    """Local filesystem implementation of media storage."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def initialize(self) -> None:
        """Create the media root directory."""
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        """Get the directory for a session's media files."""
        return self.root / session_id

    def put(
        self,
        session_id: str,
        file_id: str,
        data: bytes,
        mime: str = "application/octet-stream",
    ) -> MediaInfo:
        """Store a file and return its metadata."""
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        ext = _guess_extension(mime)
        filename = f"{file_id}{ext}"
        file_path = session_dir / filename

        with open(file_path, "wb") as f:
            f.write(data)

        rel_path = f"{session_id}/{filename}"
        return MediaInfo(
            file_id=file_id,
            path=rel_path,
            mime=mime,
            sha256=_sha256(data),
            size=len(data),
        )

    def get(self, path: str) -> Optional[bytes]:
        """Retrieve file contents by path."""
        full_path = self.root / path
        if not full_path.exists():
            return None

        with open(full_path, "rb") as f:
            return f.read()

    def delete(self, path: str) -> bool:
        """Delete a file by path."""
        full_path = self.root / path
        if not full_path.exists():
            return False

        full_path.unlink()
        return True

    def exists(self, path: str) -> bool:
        """Check if a file exists."""
        return (self.root / path).exists()

    def get_path(self, session_id: str, file_id: str) -> Path:
        """Get the full filesystem path for a file ( searches for any extension)."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return session_dir / f"{file_id}.bin"

        for f in session_dir.iterdir():
            if f.stem == file_id:
                return f
        return session_dir / f"{file_id}.bin"
