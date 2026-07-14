"""
PostgreSQL database backend for session persistence.

Requires: psycopg2-binary (pip install psycopg2-binary)

This is a full implementation stub — all methods are implemented
using PostgreSQL syntax. Swap from SQLite by changing the DB URL:
    postgresql://user:pass@host/logicore
"""

from __future__ import annotations

import json
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import DatabaseBackend

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class PostgresBackend(DatabaseBackend):
    """PostgreSQL implementation of the database backend."""

    def __init__(self, url: str, password: str = "", pool_size: int = 5):
        if not HAS_PSYCOPG2:
            raise ImportError(
                "psycopg2 is required for PostgreSQL backend. "
                "Install with: pip install psycopg2-binary"
            )
        self.url = url
        self.password = password
        self.pool_size = pool_size
        self._conn = None

    def initialize(self) -> None:
        """Connect to PostgreSQL and create tables."""
        connect_str = self.url
        if self.password and "password" not in connect_str:
            # Insert password into URL
            connect_str = connect_str.replace("://", f":{self.password}@", 1)

        self._conn = psycopg2.connect(connect_str)
        self._conn.autocommit = False
        self._ensure_tables()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_tables(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    session_messages JSONB DEFAULT '[]',
                    snapshot_path TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT DEFAULT '',
                    has_attachments BOOLEAN DEFAULT FALSE,
                    provider TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    revision INTEGER DEFAULT 1,
                    context TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_telemetry (
                    session_id TEXT PRIMARY KEY,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    tool_calls INTEGER DEFAULT 0,
                    api_calls INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0,
                    cost_status TEXT DEFAULT 'unknown',
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_syncs (
                    session_id TEXT PRIMARY KEY,
                    enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC)
            """)
            cur.execute("SELECT COUNT(*) FROM schema_version")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "INSERT INTO schema_version (version) VALUES (1)"
                )
            self._conn.commit()

    def reload_schema(self) -> None:
        """Notify Supabase PostgREST to reload its schema cache.

        Only relevant on Supabase (which fronts Postgres with PostgREST and
        caches the schema). Plain PostgreSQL hosts (Neon, RDS, Cloud SQL,
        Azure, CockroachDB, ...) don't run PostgREST, so we skip the NOTIFY
        entirely — it would be a harmless no-op but serves no purpose there.

        Uses a dedicated connection with autocommit so the NOTIFY fires
        immediately, independent of the main connection's transaction state.
        Retries up to 3 times with backoff to handle transient PgBouncer
        routing issues (transaction mode may route NOTIFY to a backend
        PostgREST isn't listening on — retrying increases hit rate).
        """
        from urllib.parse import urlparse

        host = (urlparse(self.url).hostname or "").lower()
        if "supabase.co" not in host and "supabase.in" not in host:
            return

        for attempt in range(3):
            try:
                conn = psycopg2.connect(self.url)
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_notify('pgrst', 'reload schema')")
                conn.close()
                logger.debug("[Storage] PostgREST schema reload notified (attempt %d)", attempt + 1)
                return
            except Exception as e:
                logger.debug("[Storage] Schema reload attempt %d failed: %s", attempt + 1, e)
                time.sleep(0.5 * (attempt + 1))
        logger.debug("[Storage] Schema reload notifications sent (best-effort, may need manual SQL Editor trigger)")

    def create_session(self, session_id, provider="", model="", description=""):
        now = datetime.now().isoformat()
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions (session_id, created_at, updated_at, provider, model, description)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (session_id) DO NOTHING""",
                (session_id, now, now, provider, model, description),
            )
            cur.execute(
                """INSERT INTO session_telemetry (session_id)
                   VALUES (%s) ON CONFLICT (session_id) DO NOTHING""",
                (session_id,),
            )
            self._conn.commit()

    def session_exists(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM sessions WHERE session_id = %s", (session_id,))
            return cur.fetchone() is not None

    def save_messages(self, session_id, messages):
        now = datetime.now().isoformat()
        from ..json_utils import dumps
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET session_messages = %s, updated_at = %s WHERE session_id = %s",
                (dumps(messages), now, session_id),
            )
            self._conn.commit()

    def load_messages(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute("SELECT session_messages FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return row[0] if isinstance(row[0], list) else json.loads(row[0])

    def load_metadata(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute("SELECT context FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            if row is None or not row[0]:
                return None
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                return None

    def update_session(self, session_id, description=None, provider=None, model=None,
                       has_attachments=None, snapshot_path=None, context=None):
        updates, params = [], []
        if description is not None:
            updates.append("description = %s"); params.append(description)
        if provider is not None:
            updates.append("provider = %s"); params.append(provider)
        if model is not None:
            updates.append("model = %s"); params.append(model)
        if has_attachments is not None:
            updates.append("has_attachments = %s"); params.append(has_attachments)
        if snapshot_path is not None:
            updates.append("snapshot_path = %s"); params.append(snapshot_path)
        if context is not None:
            updates.append("context = %s"); params.append(context)
        if not updates:
            return
        updates.append("updated_at = %s")
        params.append(datetime.now().isoformat())
        params.append(session_id)
        with self._conn.cursor() as cur:
            cur.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = %s", params)
            self._conn.commit()

    def increment_revision(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revision = revision + 1, updated_at = %s WHERE session_id = %s RETURNING revision",
                (datetime.now().isoformat(), session_id),
            )
            row = cur.fetchone()
            self._conn.commit()
            return row[0] if row else 1

    def list_sessions(self, limit=50):
        with self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """SELECT session_id, created_at, updated_at, description,
                          provider, model, revision, has_attachments, snapshot_path
                   FROM sessions ORDER BY updated_at DESC LIMIT %s""",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def delete_session(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM session_telemetry WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def save_telemetry(self, session_id, input_tokens=0, output_tokens=0,
                       cache_read_tokens=0, cache_write_tokens=0,
                       reasoning_tokens=0, tool_calls=0, api_calls=0,
                       estimated_cost_usd=0, cost_status="unknown"):
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO session_telemetry
                   (session_id, input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, reasoning_tokens, tool_calls, api_calls,
                    estimated_cost_usd, cost_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (session_id) DO UPDATE SET
                     input_tokens = session_telemetry.input_tokens + EXCLUDED.input_tokens,
                     output_tokens = session_telemetry.output_tokens + EXCLUDED.output_tokens,
                     cache_read_tokens = session_telemetry.cache_read_tokens + EXCLUDED.cache_read_tokens,
                     cache_write_tokens = session_telemetry.cache_write_tokens + EXCLUDED.cache_write_tokens,
                     reasoning_tokens = session_telemetry.reasoning_tokens + EXCLUDED.reasoning_tokens,
                     tool_calls = session_telemetry.tool_calls + EXCLUDED.tool_calls,
                     api_calls = session_telemetry.api_calls + EXCLUDED.api_calls,
                     estimated_cost_usd = session_telemetry.estimated_cost_usd + EXCLUDED.estimated_cost_usd,
                     cost_status = CASE
                       WHEN EXCLUDED.cost_status = 'actual' THEN 'actual'
                       WHEN EXCLUDED.cost_status = 'estimated' AND session_telemetry.cost_status != 'actual' THEN 'estimated'
                       ELSE session_telemetry.cost_status
                     END""",
                (session_id, input_tokens, output_tokens, cache_read_tokens,
                 cache_write_tokens, reasoning_tokens, tool_calls, api_calls,
                 estimated_cost_usd, cost_status),
            )
            self._conn.commit()

    def load_telemetry(self, session_id):
        with self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM session_telemetry WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_schema_version(self):
        with self._conn.cursor() as cur:
            cur.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            return row[0] if row and row[0] else 0

    def set_schema_version(self, version):
        with self._conn.cursor() as cur:
            cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))
            self._conn.commit()

    def add_pending_sync(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO pending_syncs (session_id, enqueued_at)
                   VALUES (%s, %s) ON CONFLICT (session_id) DO UPDATE SET enqueued_at = EXCLUDED.enqueued_at""",
                (session_id, datetime.now().isoformat()),
            )
            self._conn.commit()

    def remove_pending_sync(self, session_id):
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM pending_syncs WHERE session_id = %s", (session_id,))
            self._conn.commit()

    def get_pending_syncs(self):
        with self._conn.cursor() as cur:
            cur.execute("SELECT session_id FROM pending_syncs ORDER BY enqueued_at")
            return [row[0] for row in cur.fetchall()]
