"""
SnapshotWorker: Stateless background worker for snapshot synchronization.

Design:
- Queue-based: receives session_ids to sync
- Stateless: always reads from SQL, serializes, overwrites snapshot
- Non-daemon thread: survives process exit for graceful shutdown
- Crash recovery: pending_syncs table tracks unfinished syncs
- On startup: recovers any syncs that were interrupted

Usage:
    worker = SnapshotWorker(db, snapshot_backend)
    worker.start()
    worker.enqueue("session-123")  # non-blocking
    worker.stop()  # drains queue, then stops
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, List

from .manifest import SessionManifest

if TYPE_CHECKING:
    from logicore.storage.db.base import DatabaseBackend
    from logicore.storage.snapshot.base import SnapshotBackend

logger = logging.getLogger(__name__)


class SnapshotWorker:
    """
    Background worker that syncs SQL state to snapshot files.

    Stateless design:
    - Each job reads the FULL session from SQL
    - Builds a complete manifest
    - Overwrites the snapshot file (never patches)
    - If it fails, the next attempt will also be a full read

    Thread model:
    - Single non-daemon thread processes the queue
    - enqueue() is non-blocking (puts to queue)
    - stop() signals shutdown and waits for queue drain
    - atexit handler ensures graceful shutdown on process exit

    Crash recovery:
    - Each enqueue() writes to pending_syncs table in SQL
    - After successful sync, pending_sync is removed
    - On startup, check for pending syncs and process them
    """

    def __init__(
        self,
        db: "DatabaseBackend",
        snapshot: "SnapshotBackend",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self._db = db
        self._snapshot = snapshot
        self._max_retries = max_retries
        self._retry_delay = retry_delay

        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._drained = threading.Event()
        self._drained.set()
        self._processing = False
        self._atexit_registered = False

    def start(self) -> None:
        """Start the background worker thread and recover pending syncs."""
        if self._running:
            return

        self._running = True
        self._drained.clear()

        # Register atexit handler for graceful shutdown
        if not self._atexit_registered:
            atexit.register(self._atexit_shutdown)
            self._atexit_registered = True

        self._thread = threading.Thread(
            target=self._run,
            name="snapshot-worker",
            daemon=False,  # Non-daemon: survives process exit
        )
        self._thread.start()
        logger.debug("[WORKER] started background snapshot worker | thread=%s", self._thread.name)

        # Recover any pending syncs from previous crash
        self._recover_pending_syncs()

    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop the worker. Drains remaining items before stopping.

        Args:
            timeout: Max seconds to wait for queue to drain.
        """
        if not self._running:
            return

        self._running = False
        # Put sentinel to wake up the worker loop
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _atexit_shutdown(self) -> None:
        """atexit handler: drain queue on process exit."""
        if not self._running:
            return

        logger.debug("atexit: draining snapshot queue...")
        self._running = False
        # Put sentinel to stop the loop after current item
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

        # Best-effort drain; do not block process exit indefinitely.
        # Any unfinished syncs remain in pending_syncs and recover on restart.
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def enqueue(self, session_id: str) -> None:
        """
        Enqueue a session for snapshot sync. Non-blocking.

        Writes to pending_syncs table for crash recovery.
        If the session is already queued, the duplicate is silently
        ignored (the worker will process the latest state anyway
        since it reads from SQL each time).
        """
        if not self._running:
            return

        # Mark as pending in SQL for crash recovery
        try:
            self._db.add_pending_sync(session_id)
        except Exception:
            pass

        try:
            self._drained.clear()
            self._queue.put_nowait(session_id)
        except Exception:
            pass

    def wait_drained(self, timeout: float = 30.0) -> bool:
        """Wait until the queue is empty. Returns True if drained."""
        return self._drained.wait(timeout=timeout)

    @property
    def is_running(self) -> bool:
        """Check if the worker is running."""
        return self._running

    @property
    def pending_count(self) -> int:
        """Number of pending items in the queue."""
        return self._queue.qsize()

    def _recover_pending_syncs(self) -> None:
        """Recover pending syncs from a previous crash."""
        try:
            pending = self._db.get_pending_syncs()
            if pending:
                logger.info(
                    "Recovering %d pending snapshot sync(s): %s",
                    len(pending), pending,
                )
                for session_id in pending:
                    try:
                        self._queue.put_nowait(session_id)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("Failed to recover pending syncs: %s", e)

    def _run(self) -> None:
        """Worker loop: process items from the queue."""
        while self._running:
            try:
                session_id = self._queue.get(timeout=1.0)
            except Exception:
                # Queue empty or timeout — check if we should stop
                if self._queue.empty() and not self._processing:
                    self._drained.set()
                continue

            # Sentinel (None) means stop
            if session_id is None:
                self._queue.task_done()
                self._drained.set()
                break

            self._processing = True
            try:
                self._sync_session(session_id)
            except Exception as e:
                logger.warning(
                    "Snapshot sync failed for session %s: %s",
                    session_id, e,
                )
            finally:
                self._processing = False
                self._queue.task_done()
                if self._queue.empty():
                    self._drained.set()

    def _sync_session(self, session_id: str) -> None:
        """
        Stateless sync: read SQL → build manifest → write snapshot.

        Retries on transient failures (e.g., file I/O errors).
        After successful sync, removes from pending_syncs.
        """
        last_error = None

        for attempt in range(1, self._max_retries + 1):
            try:
                self._do_sync(session_id)
                # Success — remove from pending syncs
                try:
                    self._db.remove_pending_sync(session_id)
                except Exception:
                    pass
                return
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    time.sleep(self._retry_delay * attempt)

        # All retries exhausted — stays in pending_syncs for next recovery
        logger.warning(
            "Snapshot sync failed after %d attempts for session %s: %s",
            self._max_retries, session_id, last_error,
        )

    def _do_sync(self, session_id: str) -> None:
        """
        Perform the actual sync. Stateless — always full read + full write.
        """
        logger.debug(f"[WORKER] syncing snapshot | session={session_id} | reading from SQL (source of truth)")
        # Read from SQL (canonical source of truth)
        messages = self._db.load_messages(session_id)
        if messages is None:
            # Session doesn't exist in DB — skip and clean up
            try:
                self._db.remove_pending_sync(session_id)
            except Exception:
                pass
            logger.debug(f"[WORKER] snapshot sync skipped | session={session_id} | not in SQL")
            return

        telemetry = self._db.load_telemetry(session_id) or {}

        # Load session metadata
        sessions = self._db.list_sessions(limit=10000)
        meta = next(
            (s for s in sessions if s["session_id"] == session_id),
            {},
        )

        # Build typed manifest
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

        # Write to snapshot (full overwrite, never patch)
        self._snapshot.save_snapshot(session_id, manifest.to_dict())

        # Update snapshot_path in SQL
        snapshot_path = str(self._snapshot.get_snapshot_path(session_id))
        self._db.update_session(session_id, snapshot_path=snapshot_path)
        logger.debug(f"[WORKER] snapshot written to disk | session={session_id} | path={snapshot_path}")
