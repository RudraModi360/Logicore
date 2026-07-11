# Backend Extension Guide — Plug In Any Cloud Database

This document is for the **developer** persona: *"Logicore doesn't support my
database (MySQL, Turso/libSQL, Cloudflare D1, …). How do I add support without
forking the agent, snapshot, or media tiers?"*

The answer: **implement one class — `DatabaseBackend` — and inject it.** That is
the only open-ended wire you need. Everything above it (the agent, the snapshot
worker, the media tier) talks to the backend only through this interface, so a
new DB is a single, isolated file.

---

## 1. The architecture in one paragraph

```
Agent / Session
      │  (uses)
      ▼
StorageManager  ──►  DatabaseBackend  (YOU implement this for a new DB)
      │
      ├─ SnapshotBackend   (JSON manifests — already generic, filesystem only)
      └─ MediaBackend      (binary bytes — local or S3 — already generic)
```

`StorageManager` owns the lifecycle. It calls methods on a `DatabaseBackend`
for everything relational. You never touch `agent/base.py`, the snapshot worker,
or the media tier. You write **one** class and register it.

---

## 2. The injection wire (three ways to plug in)

The constructor already accepts a pre-built backend. When you pass one, the
URL-based auto-detection in `initialize()` is **skipped**.

### Way A — direct `StorageManager`

```python
from logicore.storage import StorageManager, StorageConfig
from my_package.turso_backend import TursoBackend

config = StorageConfig()                       # DB url is irrelevant here
manager = StorageManager(config, db=TursoBackend(url="...", token="..."))
manager.initialize()                           # uses YOUR backend
```

### Way B — the `create_storage()` convenience fn

```python
from logicore.storage import create_storage
from my_package.turso_backend import TursoBackend

mgr = create_storage(db=TursoBackend(url="...", token="..."))
```

### Way C — your own settings override

If you build `StorageConfig` yourself, just hand the backend to `StorageManager`
as in Way A. `StorageConfig.database.url` is ignored whenever `db=` is supplied.

> **Rule:** any `DatabaseBackend` subclass instance works, regardless of the
> `LOGICORE_STORAGE_DB_URL` value. The URL only matters for the built-in
> auto-detection path.

---

## 3. The contract — every method you must implement

`DatabaseBackend` (in `logicore/storage/db/base.py`) declares these abstract
methods. Implement all of them. `reload_schema()` already has a no-op default —
override it only if your platform has a schema-cache quirk (like Supabase).

| Method | Signature | What it must do |
|--------|-----------|-----------------|
| `initialize` | `() -> None` | Create tables if missing. Called once by `StorageManager.initialize()`. |
| `close` | `() -> None` | Release connections / pools. Called on shutdown. |
| `create_session` | `(session_id, provider="", model="", description="") -> None` | Insert a row into `sessions`. |
| `session_exists` | `(session_id) -> bool` | Return whether the session row exists. |
| `save_messages` | `(session_id, messages: List[Dict]) -> None` | Persist the full message list (replace or upsert). |
| `load_messages` | `(session_id) -> Optional[List[Dict]]` | Return the stored message list, or `None`/`[]` if absent. |
| `update_session` | `(session_id, description=None, provider=None, model=None, has_attachments=None, snapshot_path=None, context=None) -> None` | Patch the given (non-`None`) columns. `context` is a **JSON string** (tags, `last_tool_directory`, `_vfs_files`). |
| `increment_revision` | `(session_id) -> int` | Atomically `UPDATE sessions SET revision = revision + 1 ... RETURNING revision`; return new value. |
| `list_sessions` | `(limit=50) -> List[Dict]` | Return recent sessions (id, description, provider, model, updated_at, message_count, has_attachments), newest first. |
| `delete_session` | `(session_id) -> bool` | Delete session + its telemetry + pending_sync; return `True` if something was removed. |
| `save_telemetry` | `(session_id, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0, tool_calls=0, api_calls=0) -> None` | Upsert token/call counters for the session. |
| `load_telemetry` | `(session_id) -> Optional[Dict]` | Return the telemetry row, or `None`. |
| `get_schema_version` | `() -> int` | Read the single `schema_version` row; default to `0` if absent. |
| `set_schema_version` | `(version: int) -> None` | Upsert the `schema_version` row. |
| `add_pending_sync` | `(session_id) -> None` | Insert into `pending_syncs` (idempotent). |
| `remove_pending_sync` | `(session_id) -> None` | Delete from `pending_syncs`. |
| `get_pending_syncs` | `() -> List[str]` | Return all pending session IDs. |
| `reload_schema` | `() -> None` | **Optional.** No-op by default. Override only if your platform caches schema (Supabase PostgREST). |

---

## 4. Conventions you must honour (the schema contract)

Your backend is free to use any SQL dialect, but the **logical schema** must
match what the rest of the system expects:

### `sessions`
| column | type | notes |
|--------|------|-------|
| `session_id` | TEXT / VARCHAR (PK) | UUID string |
| `provider` | TEXT | e.g. `openai` |
| `model` | TEXT | e.g. `gpt-4` |
| `description` | TEXT | |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | bump on every write |
| `revision` | INTEGER | optimistic-concurrency counter |
| `has_attachments` | BOOLEAN | |
| `snapshot_path` | TEXT / NULL | |
| `context` | JSON / JSONB (TEXT is fine) | stores a JSON **string** of `{tags, last_tool_directory, _vfs_files}` |

> `context` is passed to `update_session()` already **serialized as a JSON
> string**. You may store it as a JSON column or plain TEXT — just store the
> string verbatim and return it verbatim when `load_session` reads it back.

### `messages`
- Stored **per session** as one row: `session_id` (PK) + `messages` (JSON/TEXT
  holding the full list). `save_messages` replaces; `load_messages` parses.

### `telemetry`
- One row per `session_id` with the integer counters listed in §3.

### `pending_syncs`
- Single column `session_id` (PK). Used by the snapshot worker for crash
  recovery — just store/delete/list the IDs.

### `schema_version`
- Single row (e.g. `id=1, version=N`). Start at `0` and let the manager set it.

You do **not** have to match PostgreSQL column types exactly — SQLite uses
`INTEGER`/`TEXT`, CockroachDB uses `JSONB`, etc. The system only depends on the
*names* and *semantics* above.

---

## 5. Worked example — a Turso / libSQL backend

Turso (libSQL) is a distributed SQLite reached over an **HTTP API**, not the
PostgreSQL wire protocol — so it is *not* covered by `PostgresBackend`. Below is
a complete, drop-in template implementing the contract against the libSQL HTTP
`/v1/execute` endpoint. (Swap the transport for a native driver like
`libsql-client` if you prefer; the method bodies stay the same.)

```python
# my_package/turso_backend.py
"""Drop-in DatabaseBackend for Turso / libSQL over the HTTP API."""
from __future__ import annotations
import json
import requests
from typing import Any, Dict, List, Optional
from logicore.storage.db.base import DatabaseBackend


class TursoBackend(DatabaseBackend):
    def __init__(self, url: str, token: str, db_name: str = "logicore"):
        self.url = url.rstrip("/")          # e.g. https://<org>-<db>.turso.io
        self.token = token
        self.db_name = db_name
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    # ---- low-level helpers -------------------------------------------------
    def _exec(self, sql: str, args: List[Any] = None) -> dict:
        """Run one statement against /v1/execute."""
        body = {"statements": [{"q": sql, "args": self._bind(args or [])}]}
        r = self._session.post(f"{self.url}/v1/execute", json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _batch(self, statements: List[tuple]) -> None:
        """Run several statements in one /v1/batch call (transaction)."""
        body = {"statements": [
            {"q": sql, "args": self._bind(args or [])} for sql, args in statements
        ]}
        r = self._session.post(f"{self.url}/v1/batch", json=body, timeout=60)
        r.raise_for_status()

    @staticmethod
    def _bind(args: List[Any]) -> List[dict]:
        out = []
        for a in args:
            if isinstance(a, bool):
                out.append({"type": "integer", "value": int(a)})
            elif isinstance(a, int):
                out.append({"type": "integer", "value": a})
            elif isinstance(a, float):
                out.append({"type": "float", "value": a})
            elif a is None:
                out.append({"type": "null"})
            else:
                out.append({"type": "text", "value": str(a)})
        return out

    # ---- DatabaseBackend contract -----------------------------------------
    def initialize(self) -> None:
        self._batch([
            ("CREATE TABLE IF NOT EXISTS sessions ("
             "session_id TEXT PRIMARY KEY, provider TEXT, model TEXT, "
             "description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
             "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, revision INTEGER DEFAULT 0, "
             "has_attachments INTEGER DEFAULT 0, snapshot_path TEXT, context TEXT)", (),),
            ("CREATE TABLE IF NOT EXISTS messages ("
             "session_id TEXT PRIMARY KEY, messages TEXT)", (),),
            ("CREATE TABLE IF NOT EXISTS telemetry ("
             "session_id TEXT PRIMARY KEY, input_tokens INTEGER DEFAULT 0, "
             "output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, "
             "cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0, "
             "tool_calls INTEGER DEFAULT 0, api_calls INTEGER DEFAULT 0)", (),),
            ("CREATE TABLE IF NOT EXISTS pending_syncs (session_id TEXT PRIMARY KEY)", (),),
            ("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER)", (),),
        ])

    def close(self) -> None:
        self._session.close()

    def create_session(self, session_id, provider="", model="", description="") -> None:
        self._exec(
            "INSERT OR IGNORE INTO sessions (session_id, provider, model, description) "
            "VALUES (?, ?, ?, ?)",
            [session_id, provider, model, description],
        )

    def session_exists(self, session_id) -> bool:
        res = self._exec("SELECT 1 FROM sessions WHERE session_id = ?", [session_id])
        return bool(self._rows(res))

    def save_messages(self, session_id, messages) -> None:
        self._exec(
            "INSERT INTO messages (session_id, messages) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages = excluded.messages",
            [session_id, json.dumps(messages)],
        )
        self._exec("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                   [session_id])

    def load_messages(self, session_id):
        res = self._exec("SELECT messages FROM messages WHERE session_id = ?", [session_id])
        rows = self._rows(res)
        if not rows:
            return None
        return json.loads(rows[0][0])

    def update_session(self, session_id, description=None, provider=None, model=None,
                       has_attachments=None, snapshot_path=None, context=None) -> None:
        sets, args = [], []
        if description is not None:   sets.append("description = ?"); args.append(description)
        if provider is not None:      sets.append("provider = ?");    args.append(provider)
        if model is not None:         sets.append("model = ?");       args.append(model)
        if has_attachments is not None:
            sets.append("has_attachments = ?"); args.append(int(has_attachments))
        if snapshot_path is not None: sets.append("snapshot_path = ?"); args.append(snapshot_path)
        if context is not None:       sets.append("context = ?");     args.append(context)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        args.append(session_id)
        self._exec(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?", args)

    def increment_revision(self, session_id) -> int:
        self._exec("UPDATE sessions SET revision = revision + 1 WHERE session_id = ?",
                   [session_id])
        res = self._exec("SELECT revision FROM sessions WHERE session_id = ?", [session_id])
        return int(self._rows(res)[0][0])

    def list_sessions(self, limit=50):
        res = self._exec(
            "SELECT session_id, description, provider, model, updated_at, "
            "has_attachments FROM sessions ORDER BY updated_at DESC LIMIT ?",
            [limit],
        )
        out = []
        for row in self._rows(res):
            out.append({
                "session_id": row[0], "description": row[1], "provider": row[2],
                "model": row[3], "updated_at": row[4], "has_attachments": bool(row[5]),
                "message_count": 0,
            })
        return out

    def delete_session(self, session_id) -> bool:
        existed = self.session_exists(session_id)
        self._batch([
            ("DELETE FROM messages WHERE session_id = ?", [session_id]),
            ("DELETE FROM telemetry WHERE session_id = ?", [session_id]),
            ("DELETE FROM pending_syncs WHERE session_id = ?", [session_id]),
            ("DELETE FROM sessions WHERE session_id = ?", [session_id]),
        ])
        return existed

    def save_telemetry(self, session_id, input_tokens=0, output_tokens=0,
                       cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                       tool_calls=0, api_calls=0) -> None:
        self._exec(
            "INSERT INTO telemetry (session_id, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, reasoning_tokens, tool_calls, "
            "api_calls) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "input_tokens=excluded.input_tokens + input_tokens, "
            "output_tokens=excluded.output_tokens + output_tokens, "
            "cache_read_tokens=excluded.cache_read_tokens + cache_read_tokens, "
            "cache_write_tokens=excluded.cache_write_tokens + cache_write_tokens, "
            "reasoning_tokens=excluded.reasoning_tokens + reasoning_tokens, "
            "tool_calls=excluded.tool_calls + tool_calls, "
            "api_calls=excluded.api_calls + api_calls",
            [session_id, input_tokens, output_tokens, cache_read_tokens,
             cache_write_tokens, reasoning_tokens, tool_calls, api_calls],
        )

    def load_telemetry(self, session_id):
        res = self._exec("SELECT * FROM telemetry WHERE session_id = ?", [session_id])
        rows = self._rows(res)
        if not rows:
            return None
        cols = ["session_id", "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "tool_calls", "api_calls"]
        return dict(zip(cols, rows[0]))

    def get_schema_version(self) -> int:
        res = self._exec("SELECT version FROM schema_version WHERE id = 1")
        rows = self._rows(res)
        return int(rows[0][0]) if rows else 0

    def set_schema_version(self, version) -> None:
        self._exec(
            "INSERT INTO schema_version (id, version) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
            [version],
        )

    def add_pending_sync(self, session_id) -> None:
        self._exec("INSERT OR IGNORE INTO pending_syncs (session_id) VALUES (?)", [session_id])

    def remove_pending_sync(self, session_id) -> None:
        self._exec("DELETE FROM pending_syncs WHERE session_id = ?", [session_id])

    def get_pending_syncs(self) -> List[str]:
        res = self._exec("SELECT session_id FROM pending_syncs")
        return [r[0] for r in self._rows(res)]

    # reload_schema() left as default no-op — Turso has no external schema cache.

    # ---- response parsing helper ------------------------------------------
    @staticmethod
    def _rows(resp: dict) -> List[list]:
        """Extract result rows from a libSQL /v1/execute JSON response."""
        try:
            stmt = resp["results"][0]
            cols = [c["name"] for c in stmt.get("columns", [])]
            return [row for row in stmt.get("rows", [])]
        except (KeyError, IndexError):
            return []
```

### How to use it

```python
from logicore.storage import StorageManager, StorageConfig
from my_package.turso_backend import TursoBackend

cfg = StorageConfig()
mgr = StorageManager(cfg, db=TursoBackend(
    url="https://<org>-<db>.turso.io",
    token="<turso-auth-token>",
))
mgr.initialize()
mgr.save_session("s1", messages=[{"role": "user", "content": "hi"}],
                 provider="openai", model="gpt-4")
```

That's the entire integration. No other file changes.

---

## 6. Connection-management patterns

- **Pooled relational DBs (Postgres family):** open a pool in `initialize()`,
  hand out connections per call, return them in `close()`. See
  `PostgresBackend` for the `psycopg2.pool.SimpleConnectionPool` pattern.
- **HTTP/stateless DBs (Turso, D1):** keep one `requests.Session()` (reuses TCP
  + TLS), no pool needed. Each method is one or more HTTP calls.
- **Embedded (SQLite, libSQL file):** open the file once in `initialize()`,
  serialize writes with a lock if multi-threaded.
- **Always make writes atomic** — wrap `save_messages` + `increment_revision` +
  `add_pending_sync` so a crash mid-write can't leave an inconsistent state.

---

## 7. Testing your backend

A new backend must satisfy the same contract the built-in backends are tested
against. Minimum checklist (mirror `tests/test_storage_*.py`):

```python
def test_roundtrip(backend):
    mgr = StorageManager(StorageConfig(), db=backend)
    mgr.initialize()
    mgr.save_session("a", messages=[{"role": "user", "content": "hi"}],
                     provider="p", model="m",
                     metadata={"tags": ["x"], "last_tool_directory": "/tmp"})
    assert mgr.load_session("a") == [{"role": "user", "content": "hi"}]
    assert mgr.load_session_metadata("a")["tags"] == ["x"]
    assert mgr.session_exists("a") is True
    mgr.delete_session("a")
    assert mgr.session_exists("a") is False
```

Also verify: revision increments monotonically, telemetry upserts/accumulates,
`pending_syncs` is cleared after snapshot, and `context` (tags + VFS refs)
survives a save/load cycle.

---

## 8. Upstreaming (optional)

If your backend is generally useful (MySQL, Turso, D1), it can become a first-
class backend:

1. Add the subclass under `logicore/storage/db/<name>.py`.
2. Add explicit URL-scheme detection in `StorageManager.initialize()`
   (`mysql://` → `MySQLBackend`, `turso://`/`libsql://` → `TursoBackend`, …)
   instead of the current binary `is_postgresql` check, so unknown schemes fail
   loudly instead of silently falling through to SQLite.
3. Add `DatabaseConfig.backend` override field for forcing a backend regardless
   of URL.
4. Add tests under `tests/` and a section to
   `SESSION_PERSISTENCE_CLOUD_DBS.md`.

Until then, the injection wire (§2) lets you ship your backend **today** with
zero changes to the framework.
