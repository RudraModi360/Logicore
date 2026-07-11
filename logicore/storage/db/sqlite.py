"""
SQLite database backend for session persistence.

Canonical source of truth for all session data.
Schema versioned via schema_version table for future migrations.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

from .base import DatabaseBackend


CURRENT_SCHEMA_VERSION = 1


class SqliteBackend(DatabaseBackend):
    """SQLite implementation of the database backend."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        """Create database file, directories, and tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_tables()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _get_connection(self):
        """Get a database connection (yields existing or creates temp)."""
        if self._conn:
            yield self._conn
        else:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def _ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    session_messages TEXT DEFAULT '[]',
                    snapshot_path TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT DEFAULT '',
                    has_attachments INTEGER DEFAULT 0,
                    provider TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    revision INTEGER DEFAULT 1,
                    context TEXT DEFAULT ''
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_telemetry (
                    session_id TEXT PRIMARY KEY,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    tool_calls INTEGER DEFAULT 0,
                    api_calls INTEGER DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC)
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_syncs (
                    session_id TEXT PRIMARY KEY,
                    enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Set schema version if not present
            cursor.execute("SELECT COUNT(*) FROM schema_version")
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (CURRENT_SCHEMA_VERSION,),
                )

            conn.commit()

    def create_session(
        self,
        session_id: str,
        provider: str = "",
        model: str = "",
        description: str = "",
    ) -> None:
        """Create a new session record."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id, created_at, updated_at, provider, model, description)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, now, now, provider, model, description),
            )
            conn.execute(
                """INSERT OR IGNORE INTO session_telemetry (session_id)
                   VALUES (?)""",
                (session_id,),
            )
            conn.commit()

    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.fetchone() is not None

    def save_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Save session messages (JSON)."""
        now = datetime.now().isoformat()
        from ..json_utils import dumps
        with self._get_connection() as conn:
            conn.execute(
                """UPDATE sessions
                   SET session_messages = ?, updated_at = ?
                   WHERE session_id = ?""",
                (dumps(messages), now, session_id),
            )
            conn.commit()

    def load_messages(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """Load session messages."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT session_messages FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            try:
                return json.loads(row["session_messages"])
            except (json.JSONDecodeError, TypeError):
                return []

    def load_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session metadata from the context column."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT context FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None or not row["context"]:
                return None
            try:
                return json.loads(row["context"])
            except (json.JSONDecodeError, TypeError):
                return None

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
        updates = []
        params = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if provider is not None:
            updates.append("provider = ?")
            params.append(provider)
        if model is not None:
            updates.append("model = ?")
            params.append(model)
        if has_attachments is not None:
            updates.append("has_attachments = ?")
            params.append(1 if has_attachments else 0)
        if snapshot_path is not None:
            updates.append("snapshot_path = ?")
            params.append(snapshot_path)
        if context is not None:
            updates.append("context = ?")
            params.append(context)

        if not updates:
            return

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(session_id)

        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
                params,
            )
            conn.commit()

    def increment_revision(self, session_id: str) -> int:
        """Increment and return the new revision number."""
        with self._get_connection() as conn:
            conn.execute(
                """UPDATE sessions
                   SET revision = revision + 1, updated_at = ?
                   WHERE session_id = ?""",
                (datetime.now().isoformat(), session_id),
            )
            cursor = conn.execute(
                "SELECT revision FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            conn.commit()
            return row["revision"] if row else 1

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List sessions ordered by last updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT session_id, created_at, updated_at, description,
                          provider, model, revision, has_attachments, snapshot_path
                   FROM sessions
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (limit,),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its telemetry."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM session_telemetry WHERE session_id = ?",
                (session_id,),
            )
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

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
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO session_telemetry
                   (session_id, input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, reasoning_tokens, tool_calls, api_calls)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens,
                     cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,
                     cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
                     reasoning_tokens = reasoning_tokens + excluded.reasoning_tokens,
                     tool_calls = tool_calls + excluded.tool_calls,
                     api_calls = api_calls + excluded.api_calls""",
                (
                    session_id, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, tool_calls, api_calls,
                ),
            )
            conn.commit()

    def load_telemetry(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load telemetry data for a session."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT input_tokens, output_tokens, cache_read_tokens,
                          cache_write_tokens, reasoning_tokens, tool_calls, api_calls
                   FROM session_telemetry
                   WHERE session_id = ?""",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    def get_schema_version(self) -> int:
        """Get the current schema version."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(version) as v FROM schema_version"
            )
            row = cursor.fetchone()
            return row["v"] if row and row["v"] else 0

    def set_schema_version(self, version: int) -> None:
        """Set the schema version."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (version,),
            )
            conn.commit()

    def add_pending_sync(self, session_id: str) -> None:
        """Mark a session as needing snapshot sync."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_syncs (session_id, enqueued_at)
                   VALUES (?, ?)""",
                (session_id, datetime.now().isoformat()),
            )
            conn.commit()

    def remove_pending_sync(self, session_id: str) -> None:
        """Clear a session from pending syncs (after snapshot written)."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pending_syncs WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()

    def get_pending_syncs(self) -> List[str]:
        """Get all session IDs that need snapshot sync (for crash recovery)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT session_id FROM pending_syncs ORDER BY enqueued_at"
            )
            return [row["session_id"] for row in cursor.fetchall()]
