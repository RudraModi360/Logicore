"""
Filesystem snapshot backend.

Stateless: reads from SQL, serializes, overwrites snapshot file.
Each session gets:
    ~/.logicore/snapshots/{session_id}/session.json         (latest)
    ~/.logicore/snapshots/{session_id}/session_v{N}.json    (versioned)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import SnapshotBackend


class FilesystemSnapshotBackend(SnapshotBackend):
    """Local filesystem implementation of snapshot storage with versioning."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def initialize(self) -> None:
        """Create the snapshot root directory."""
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        """Get the directory for a session's snapshot."""
        return self.root / session_id

    def _session_file(self, session_id: str) -> Path:
        """Get the latest snapshot file path for a session."""
        return self._session_dir(session_id) / "session.json"

    def _versioned_file(self, session_id: str, revision: int) -> Path:
        """Get a versioned snapshot file path."""
        return self._session_dir(session_id) / f"session_v{revision}.json"

    def save_snapshot(self, session_id: str, manifest: Dict[str, Any]) -> None:
        """Save a session snapshot manifest.

        Writes both:
        - ``session.json`` — always the latest (overwritten)
        - ``session_v{revision}.json`` — versioned copy (preserved)
        """
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Write the "latest" pointer (always overwritten)
        snapshot_file = self._session_file(session_id)
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

        # Write a versioned copy (preserved for history)
        revision = manifest.get("revision", 1)
        versioned = self._versioned_file(session_id, revision)
        with open(versioned, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    def load_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load the latest session snapshot manifest."""
        snapshot_file = self._session_file(session_id)
        if not snapshot_file.exists():
            return None

        try:
            with open(snapshot_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def load_version(self, session_id: str, revision: int) -> Optional[Dict[str, Any]]:
        """Load a specific versioned snapshot."""
        versioned = self._versioned_file(session_id, revision)
        if not versioned.exists():
            return None
        try:
            with open(versioned, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def list_versions(self, session_id: str) -> List[Dict[str, Any]]:
        """List all versioned snapshots for a session (newest first)."""
        session_dir = self._session_dir(session_id)
        versions = []
        for f in sorted(session_dir.glob("session_v*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                versions.append({
                    "revision": data.get("revision", 0),
                    "file": str(f),
                    "messages": len(data.get("messages", [])),
                    "synced_at": data.get("synced_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return versions

    def delete_snapshot(self, session_id: str) -> bool:
        """Delete all snapshots (latest + versions) for a session."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return False

        import shutil
        shutil.rmtree(session_dir)
        return True

    def snapshot_exists(self, session_id: str) -> bool:
        """Check if a snapshot exists."""
        return self._session_file(session_id).exists()

    def get_snapshot_path(self, session_id: str) -> Path:
        """Get the file path for the latest session snapshot."""
        return self._session_file(session_id)
