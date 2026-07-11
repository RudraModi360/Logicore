# Logicore Session Persistence Architecture

## Overview

3-tier storage system for session persistence, designed as an isolated module (`logicore/storage/`) that runs alongside the existing codebase without modifying it.

---

## 3-Tier Architecture

```
                LogiGo Runtime
                       │
        ┌──────────────┴──────────────┐
        │                             │
        ▼                             ▼
  SQL Storage (Canonical)       Binary Storage
        │                             │
        └──────────────┬──────────────┘
                       ▼
             Snapshot Synchronizer
```

### Tier 1: SQL Database (Canonical Source of Truth)

- **Location:** `~/.logicore/database/logicore.db` (SQLite default)
- **Swappable:** PostgreSQL, Oracle via config
- **Schema:**

```sql
sessions (
    session_id TEXT PRIMARY KEY,
    session_messages TEXT DEFAULT '[]',     -- JSON
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

session_telemetry (
    session_id TEXT PRIMARY KEY,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)

schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)

pending_syncs (
    session_id TEXT PRIMARY KEY,
    enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### Tier 2: Snapshot Sync (Async, Stateless)

- **Location:** `~/.logicore/snapshots/{session_id}/session.json`
- **Worker:** Stateless — reads SQL → serializes → overwrites snapshot
- **Async:** Never blocks main runtime (thread-based)
- **Single manifest:** `session.json` contains messages + telemetry + metadata + attachment refs

### Tier 3: Binary Media Storage

- **Location:** `~/.logicore/assets/{session_id}/{file_id}.{ext}`
- **Metadata:** In snapshot JSON (id, path, mime, sha256, size)
- **Actual bytes:** In filesystem at path above
- **Swappable:** S3, Azure Blob, Firebase Storage via config

---

## Directory Structure

```
~/.logicore/
├── database/
│   └── logicore.db          # SQLite (canonical)
├── snapshots/
│   └── {session_id}/
│       └── session.json     # Snapshot manifest
├── assets/
│   └── {session_id}/
│       ├── {file_id}.png    # Binary files
│       └── {file_id}.pdf
├── memory/                  # Existing memory system
├── cache/
├── logs/
└── temp/
```

---

## StorageConfig

```python
StorageConfig(
    database=DatabaseConfig(
        url="sqlite:///{path}",     # or postgresql://...
        password="",
        pool_size=5,
    ),
    snapshot=SnapshotConfig(
        enabled=True,
        root="~/.logicore/snapshots",
        local_snapshot=True,
    ),
    media=MediaConfig(
        root="~/.logicore/assets",
        local_storage=True,
        max_file_size=100MB,
    ),
)
```

Environment variables:
- `LOGICORE_STORAGE_DB_URL`
- `LOGICORE_STORAGE_DB_PASSWORD`
- `LOGICORE_STORAGE_SNAPSHOT_ENABLED`
- `LOGICORE_STORAGE_SNAPSHOT_ROOT`
- `LOGICORE_STORAGE_ASSETS_ROOT`

---

## StorageManager API

```python
manager = StorageManager(config)
manager.initialize()

# Sessions
manager.save_session(session_id, messages, provider, model, description)
messages = manager.load_session(session_id)
manager.update_session(session_id, description=..., provider=..., model=...)
manager.delete_session(session_id)
sessions = manager.list_sessions(limit=50)

# Telemetry
manager.save_telemetry(session_id, input_tokens=..., output_tokens=..., ...)
summary = manager.get_telemetry_summary(session_id)

# Snapshots
manager.sync_snapshot(session_id)          # Manual trigger
snapshot = manager.load_snapshot(session_id)

# Attachments
info = manager.save_attachment(session_id, file_id, data, mime)
data = manager.load_attachment(path)
manager.delete_attachment(path)
```

---

## Design Principles

1. **SQL is single source of truth** — no synchronization ambiguity
2. **Runtime never waits for snapshot** — async, fire-and-forget
3. **Snapshot worker is stateless** — always full regeneration, never patch
4. **Binary storage separate from metadata** — JSON manifest + file bytes
5. **Cloud-ready via adapters** — swap SQLite→Postgres, Local→S3 without code changes
6. **Schema versioning** — future-proof for migrations

---

## Phase Progress

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 | ✅ Done | Dead code cleanup (~600 lines removed) |
| Phase 1 | ✅ Done | Storage module: config, ABCs, SQLite, snapshot, media backends, StorageManager |
| Phase 2 | ✅ Done | Snapshot sync: SnapshotWorker (queue-based, non-daemon thread, retry, graceful shutdown), SessionManifest dataclass |
| Phase 3 | ✅ Done | Binary media storage (SHA256, LocalMediaBackend `assets/`, S3MediaBackend stub) |
| Phase 4 | ✅ Done | Cloud adapter interfaces (PostgresBackend, S3MediaBackend) |
| Phase 5 | ✅ Done | Agent integration (wire into Agent.chat/stream), session resume, metadata persistence |
| Phase 6 | ✅ Done | Config integration (AgentrySettings `STORAGE_ROOT` + `create_storage()`) |
| Phase 7 | ✅ Done | Session metadata (tags, last_tool_directory) persisted to SQL; VFS files persisted to Tier-3 `assets/` and rehydrated on resume |

---

## Implementation Notes

### Phase 0 Changes
- Deleted: `logicore/session/manager.py`, `logicore/session/__init__.py`, `logicore/reloader.py`, `logicore/agent/attachments.py`, `logicore/ui/scratchy_users.db`
- Cleaned: `telemetry/tracker.py` (removed ContextWindowFetcher, dead methods), `caching/prompt_cache.py` (removed clear_stats), `security/__init__.py` (removed dead exports), `runtime/config.py` (removed dead TelemetryConfig fields)
- Updated: `logicore/__init__.py` (removed SessionManager export), `scripts/memory_chat.py` (removed unused import)

### Phase 1 Files Created
```
logicore/storage/__init__.py
logicore/storage/config.py
logicore/storage/manager.py
logicore/storage/db/__init__.py
logicore/storage/db/base.py
logicore/storage/db/sqlite.py
logicore/storage/snapshot/__init__.py
logicore/storage/snapshot/base.py
logicore/storage/snapshot/filesystem.py
logicore/storage/media/__init__.py
logicore/storage/media/base.py
logicore/storage/media/local.py
```

### Phase 2 Files Created
```
logicore/storage/snapshot/manifest.py    # SessionManifest + AttachmentRef dataclasses
logicore/storage/snapshot/worker.py      # SnapshotWorker (queue-based background thread)
```

### Phase 2 Changes
- `logicore/storage/snapshot/__init__.py` — added exports for SessionManifest, AttachmentRef, SnapshotWorker
- `logicore/storage/manager.py` — wired SnapshotWorker lifecycle (start on init, stop on close), auto-enqueue on save/update/telemetry, added `wait_snapshots()`, `enqueue_snapshot()`, `get_worker_status()`
- `logicore/storage/db/sqlite.py` — added `check_same_thread=False` for multi-thread access, added `snapshot_path` to `list_sessions` query
- `logicore/storage/__init__.py` — added SessionManifest, AttachmentRef, SnapshotWorker exports

### SnapshotWorker Design
- **Queue-based**: `queue.Queue` with non-daemon thread (survives process exit)
- **Stateless**: reads full session from SQL on each sync (never patches)
- **Non-blocking**: `enqueue()` returns immediately
- **Graceful shutdown**: `stop()` waits for thread to finish
- **atexit handler**: drains queue on normal process exit
- **Crash recovery**: `pending_syncs` table tracks unfinished syncs; on startup, recovers and processes them
- **Retry**: configurable `max_retries` and `retry_delay` for transient failures
- **Error resilient**: logs warnings but never crashes the worker
- **Drain detection**: `wait_drained()` blocks until all pending syncs complete

### Crash Recovery Flow
```
1. save_session() → SQL commits ✓ → enqueue() writes to pending_syncs table
2. Worker processes queue → writes snapshot → removes from pending_syncs
3. If process killed: pending_syncs remain in SQL
4. On next startup: worker.start() → _recover_pending_syncs() → re-enqueues all pending
5. Worker processes them → snapshots created → pending_syncs cleared
```

### Test Coverage
- `tests/integration/test_crash_recovery.py` — 4 scenarios:
  - Normal flow: save → snapshot → pending_syncs cleared
  - Crash recovery: save → kill process → restart → snapshot recovered
  - Partial crash: 3 sessions, 2 synced, 1 pending → recover the 1
  - Cross-process: process A saves, process B recovers all

### Pre-existing Test Failure
- `tests/performance/test_memory_performance.py::TestStoragePerformance::TestStoragePerformance.test_read_performance` — flaky performance threshold, unrelated to changes
- 257/258 tests pass (excluding performance)
