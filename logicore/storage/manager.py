"""
StorageManager: Orchestrator for the 3-tier storage system.

Composes DatabaseBackend + SnapshotBackend + MediaBackend.
Manages SnapshotWorker lifecycle for async snapshot sync.

Usage:
    from logicore.storage import StorageManager, StorageConfig

    config = StorageConfig()
    manager = StorageManager(config)
    manager.initialize()

    # Save a session (triggers async snapshot automatically)
    manager.save_session("session-123", messages=[...], provider="openai", model="gpt-4")

    # Load a session
    messages = manager.load_session("session-123")

    # Shutdown (drains snapshot queue)
    manager.close()
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import logging

from .config import StorageConfig
from .db.base import DatabaseBackend
from .snapshot.base import SnapshotBackend
from .snapshot.worker import SnapshotWorker
from .media.base import MediaBackend, MediaInfo

logger = logging.getLogger(__name__)


class StorageManager:
    """
    Unified storage orchestrator.

    Composes three backends:
    1. Database — canonical source of truth (SQL)
    2. Snapshot — async JSON manifests (filesystem)
    3. Media — binary file bytes (filesystem)
    """

    def __init__(
        self,
        config: StorageConfig,
        db: Optional[DatabaseBackend] = None,
        snapshot: Optional[SnapshotBackend] = None,
        media: Optional[MediaBackend] = None,
    ):
        self.config = config
        self._db = db
        self._snapshot = snapshot
        self._media = media
        self._snapshot_enabled = config.snapshot.enabled
        self._commit_callbacks: List[Callable[[str], None]] = []
        self._worker: Optional[SnapshotWorker] = None
        self._is_initialized = False

    @property
    def initialized(self) -> bool:
        return self._is_initialized

    def reload_schema(self) -> None:
        """Notify external tools to reload schema cache (e.g. Supabase PostgREST).

        Call this after creating tables outside of the normal lifecycle, or if
        the Supabase dashboard doesn't show newly created tables.
        """
        if self._db:
            self._db.reload_schema()

    def initialize(self) -> None:
        """Initialize all backends and start the snapshot worker."""
        self.config.ensure_directories()

        # Auto-detect database backend from URL
        if self._db is None:
            if self.config.database.is_postgresql:
                from .db.postgres import PostgresBackend
                self._db = PostgresBackend(
                    url=self.config.database.url,
                    password=self.config.database.password,
                    pool_size=self.config.database.pool_size,
                )
            else:
                from .db.sqlite import SqliteBackend
                self._db = SqliteBackend(self.config.database.sqlite_path)

        # Auto-detect snapshot backend
        if self._snapshot is None and self._snapshot_enabled:
            from .snapshot.filesystem import FilesystemSnapshotBackend
            self._snapshot = FilesystemSnapshotBackend(self.config.snapshot.root_path)

        # Auto-detect media backend from root path
        if self._media is None:
            media_root = self.config.media.root
            if media_root.startswith("s3://"):
                from .media.s3 import S3MediaBackend
                parts = media_root.replace("s3://", "").split("/", 1)
                bucket = parts[0]
                prefix = parts[1] if len(parts) > 1 else ""
                self._media = S3MediaBackend(
                    bucket=bucket,
                    prefix=prefix,
                    region=self.config.media.region or "us-east-1",
                    endpoint_url=self.config.media.endpoint_url,
                    aws_access_key_id=self.config.media.aws_access_key_id,
                    aws_secret_access_key=self.config.media.aws_secret_access_key,
                )
            else:
                from .media.local import LocalMediaBackend
                self._media = LocalMediaBackend(self.config.media.root_path)

        self._db.initialize()
        self._db.reload_schema()

        if self._snapshot:
            self._snapshot.initialize()

        if self._media:
            self._media.initialize()

        # Start snapshot worker if snapshot is enabled
        if self._snapshot and self._snapshot_enabled:
            self._worker = SnapshotWorker(self._db, self._snapshot)
            self._worker.start()
            logger.debug("Snapshot worker started")

        self._is_initialized = True

    def close(self, drain_timeout: float = 2.0) -> None:
        """
        Stop the snapshot worker and close all backends.

        Best-effort: waits up to ``drain_timeout`` for pending syncs, then
        stops the worker. Any unfinished syncs stay in pending_syncs and are
        recovered on the next start(), so we avoid blocking process exit.
        """
        if self._worker:
            try:
                self._worker.wait_drained(timeout=drain_timeout)
            except Exception:
                pass
            try:
                self._worker.stop(timeout=3.0)
            except Exception:
                pass
            self._worker = None
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
        self._is_initialized = False

    def shutdown(self) -> None:
        """Alias for close()."""
        self.close()

    def on_commit(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be called after a session is saved."""
        self._commit_callbacks.append(callback)

    def _fire_commit(self, session_id: str) -> None:
        """Fire commit callbacks (in background threads)."""
        for cb in self._commit_callbacks:
            try:
                thread = threading.Thread(target=cb, args=(session_id,), daemon=True)
                thread.start()
            except Exception:
                pass

    # ─── Session Operations ───────────────────────────────────────────

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        provider: str = "",
        model: str = "",
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save session messages to the database and enqueue snapshot sync."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            logger.debug(f"[TX] save_session request sent | session={session_id} | messages={len(messages)}")
            if not self._db.session_exists(session_id):
                self._db.create_session(
                    session_id,
                    provider=provider,
                    model=model,
                    description=description,
                )
            self._db.save_messages(session_id, messages)
            rev = self._db.increment_revision(session_id)
            # Persist metadata (tags, last_tool_directory, etc.) in the context column
            if metadata:
                self._db.update_session(session_id, context=json.dumps(metadata))
            logger.debug(f"[TX] save_session success | session={session_id} | stored in SQL database | revision={rev}")
        except Exception as e:
            logger.error(f"[TX] save_session FAILED | session={session_id} | error={e}")
            raise

        # Enqueue async snapshot sync (non-blocking)
        if self._worker:
            logger.debug(f"[WORKER] queued async snapshot sync | session={session_id}")
            self._worker.enqueue(session_id)

        # Fire any registered callbacks
        self._fire_commit(session_id)

    def load_session(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """Load session messages from the database."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            logger.debug(f"[TX] load_session request sent | session={session_id}")
            messages = self._db.load_messages(session_id)
            logger.debug(f"[TX] load_session success | session={session_id} | loaded={len(messages or [])} messages")
            return messages
        except Exception as e:
            logger.error(f"[TX] load_session FAILED | session={session_id} | error={e}")
            raise

    def load_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session metadata (tags, last_tool_directory, etc.) from SQL."""
        if not self._db:
            return None
        try:
            return self._db.load_metadata(session_id)
        except Exception:
            return None

    def update_session(
        self,
        session_id: str,
        description: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        context: Optional[str] = None,
    ) -> None:
        """Update session metadata and enqueue snapshot sync."""
        if not self._db:
            raise RuntimeError("Database not initialized.")

        self._db.update_session(
            session_id,
            description=description,
            provider=provider,
            model=model,
            has_attachments=has_attachments,
            context=context,
        )

        if self._worker:
            self._worker.enqueue(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session from all tiers."""
        try:
            logger.debug(f"[TX] delete_session request sent | session={session_id}")
            deleted = False
            if self._db:
                deleted = self._db.delete_session(session_id)

            if self._snapshot:
                self._snapshot.delete_snapshot(session_id)

            logger.debug(f"[TX] delete_session success | session={session_id} | deleted={deleted}")
            return deleted
        except Exception as e:
            logger.error(f"[TX] delete_session FAILED | session={session_id} | error={e}")
            raise

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all sessions."""
        if not self._db:
            return []
        logger.debug(f"[TX] list_sessions request sent | limit={limit}")
        sessions = self._db.list_sessions(limit)
        logger.debug(f"[TX] list_sessions success | count={len(sessions)}")
        return sessions

    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""
        if not self._db:
            return False
        return self._db.session_exists(session_id)

    # ─── Telemetry Operations ─────────────────────────────────────────

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
        estimated_cost_usd: float = 0,
        cost_status: str = "unknown",
    ) -> None:
        """Save telemetry counters (additive) and enqueue snapshot sync."""
        if not self._db:
            return

        try:
            logger.debug(f"[TX] save_telemetry request sent | session={session_id} | tokens={input_tokens + output_tokens}")
            self._db.save_telemetry(
                session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                reasoning_tokens=reasoning_tokens,
                tool_calls=tool_calls,
                api_calls=api_calls,
                estimated_cost_usd=estimated_cost_usd,
                cost_status=cost_status,
            )
            logger.debug(f"[TX] save_telemetry success | session={session_id} | stored in SQL database")
        except Exception as e:
            logger.error(f"[TX] save_telemetry FAILED | session={session_id} | error={e}")
            raise

        if self._worker:
            self._worker.enqueue(session_id)

    def load_telemetry(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load telemetry data for a session."""
        if not self._db:
            return None

        return self._db.load_telemetry(session_id)

    def get_telemetry_summary(self, session_id: str) -> Dict[str, Any]:
        """Get computed telemetry summary for a session."""
        raw = self.load_telemetry(session_id)
        if not raw:
            return {"session_id": session_id, "total_tokens": 0}

        total_input = raw["input_tokens"] + raw["cache_read_tokens"] + raw["cache_write_tokens"]
        total_output = raw["output_tokens"] + raw["reasoning_tokens"]
        total_tokens = total_input + total_output

        return {
            "session_id": session_id,
            "input_tokens": raw["input_tokens"],
            "output_tokens": raw["output_tokens"],
            "cache_read_tokens": raw["cache_read_tokens"],
            "cache_write_tokens": raw["cache_write_tokens"],
            "reasoning_tokens": raw["reasoning_tokens"],
            "tool_calls": raw["tool_calls"],
            "api_calls": raw["api_calls"],
            "estimated_cost_usd": raw.get("estimated_cost_usd", 0),
            "cost_status": raw.get("cost_status", "unknown"),
            "total_input": total_input,
            "total_output": total_output,
            "total_tokens": total_tokens,
        }

    # ─── Snapshot Operations ──────────────────────────────────────────

    def sync_snapshot(self, session_id: str) -> None:
        """Manually trigger a synchronous snapshot sync for a session."""
        if not self._snapshot or not self._db:
            return

        from .snapshot.manifest import SessionManifest

        messages = self._db.load_messages(session_id) or []
        telemetry = self._db.load_telemetry(session_id) or {}

        sessions = self._db.list_sessions(limit=10000)
        meta = next((s for s in sessions if s["session_id"] == session_id), {})

        manifest = SessionManifest(
            session_id=session_id,
            messages=messages,
            provider=meta.get("provider", ""),
            model=meta.get("model", ""),
            description=meta.get("description", ""),
            revision=meta.get("revision", 1),
            context=meta.get("context", ""),
            has_attachments=bool(meta.get("has_attachments", 0)),
            input_tokens=telemetry.get("input_tokens", 0),
            output_tokens=telemetry.get("output_tokens", 0),
            cache_read_tokens=telemetry.get("cache_read_tokens", 0),
            cache_write_tokens=telemetry.get("cache_write_tokens", 0),
            reasoning_tokens=telemetry.get("reasoning_tokens", 0),
            tool_calls=telemetry.get("tool_calls", 0),
            api_calls=telemetry.get("api_calls", 0),
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
            synced_at=datetime.now().isoformat(),
        )

        self._snapshot.save_snapshot(session_id, manifest.to_dict())
        self._db.update_session(
            session_id,
            snapshot_path=str(self._snapshot.get_snapshot_path(session_id)),
        )

    def enqueue_snapshot(self, session_id: str) -> None:
        """Enqueue a session for async snapshot sync (non-blocking)."""
        if self._worker:
            self._worker.enqueue(session_id)

    def wait_snapshots(self, timeout: float = 30.0) -> bool:
        """Wait for all pending snapshot syncs to complete."""
        if self._worker:
            return self._worker.wait_drained(timeout=timeout)
        return True

    def get_worker_status(self) -> Dict[str, Any]:
        """Get snapshot worker status."""
        if not self._worker:
            return {"running": False, "pending": 0}
        return {
            "running": self._worker.is_running,
            "pending": self._worker.pending_count,
        }

    def load_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a snapshot manifest."""
        if not self._snapshot:
            return None
        return self._snapshot.load_snapshot(session_id)

    # ─── Media Operations ─────────────────────────────────────────────

    def save_attachment(
        self,
        session_id: str,
        file_id: str,
        data: bytes,
        mime: str = "application/octet-stream",
    ) -> MediaInfo:
        """Save a file attachment."""
        if not self._media:
            raise RuntimeError("Media backend not initialized.")

        try:
            logger.debug(f"[TX] save_attachment request sent | session={session_id} | file={file_id} | size={len(data)} bytes")
            info = self._media.put(session_id, file_id, data, mime)
            self._db.update_session(session_id, has_attachments=True)
            logger.debug(f"[TX] save_attachment success | session={session_id} | path={info.path} | sha256={info.sha256[:8]}...")
            return info
        except Exception as e:
            logger.error(f"[TX] save_attachment FAILED | session={session_id} | error={e}")
            raise

    def load_attachment(self, path: str) -> Optional[bytes]:
        """Load a file attachment by path."""
        if not self._media:
            return None
        return self._media.get(path)

    def delete_attachment(self, path: str) -> bool:
        """Delete a file attachment."""
        if not self._media:
            return False
        return self._media.delete(path)

    # ─── Schema ───────────────────────────────────────────────────────

    def get_schema_version(self) -> int:
        """Get the current schema version."""
        if not self._db:
            return 0
        return self._db.get_schema_version()
