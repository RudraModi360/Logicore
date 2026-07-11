# Session Persistence — User Guide

> **Goal of this doc:** you know nothing about how persistence is built, but you
> want to **use it** so your agent's conversations survive restarts, crashes, and
> can live in a local file **or** a cloud database. Everything you need is here.

---

## 1. What is "session persistence"?

Every time your agent chats, it accumulates a **session** — the list of messages,
the model/provider used, tool calls, and telemetry (token usage). Without
persistence, that session vanishes when the process exits.

With persistence enabled, each session is stored in **3 tiers**:

| Tier | What | Default location | Purpose |
|------|------|------------------|---------|
| **1. SQL** | Canonical conversation (messages, telemetry, metadata) | `~/.logicore/database/logicore.db` | Source of truth |
| **2. Snapshot** | Read-only JSON copy of the session | `~/.logicore/snapshots/<id>/session.json` | Fast async mirror |
| **3. Media** | Binary files (images, attachments) | `~/.logicore/assets/<id>/` | Large blobs |

The **SQL tier is the source of truth**. Snapshots are generated *async* by a
background worker and never block your chat. If the worker crashes, the
`pending_syncs` table lets it recover on the next start.

---

## 2. The 30-second quick start

You do **not** need to understand the internals. Just pass a `storage` object to
your agent:

```python
from logicore import Agent
from logicore.storage import create_storage

storage = create_storage()                      # uses ~/.logicore by default
agent = Agent(provider="ollama", model="qwen3", storage=storage)

resp = agent.chat("Summarize this repo", session_id="my-project")
# ^ session is auto-saved to SQL + snapshot after every chat

storage.shutdown()                              # flush + close cleanly
```

That's it. After `chat()` returns, the conversation is on disk.

### Zero-config (no storage object at all)

If you don't pass `storage`, the agent simply runs in-memory as before — nothing
breaks, persistence is just disabled:

```python
agent = Agent(provider="ollama", model="qwen3")   # no storage = no persistence
```

---

## 3. The `StorageConfig` — every parameter explained

`StorageConfig` is the master config. It has **3 sub-configs**. Here is every
field, what it means, and how you'd use it.

### 3.1 `database` (Tier 1 — SQL)

| Param | Type | Default | Meaning / usage |
|-------|------|---------|-----------------|
| `url` | `str` | `~/.logicore/database/logicore.db` | **Where the SQL lives.**<br>• A plain path or `sqlite:///...` → local SQLite<br>• `postgresql://user:pass@host/db` → cloud Postgres<br>Set this to point at a *cloud* DB. |
| `password` | `str` | `""` | DB password for cloud databases (Postgres). Injected into the URL if missing. |
| `pool_size` | `int` | `5` | Connection pool size (Postgres only). Raise it for high-concurrency servers. |

```python
from logicore.storage import StorageConfig, DatabaseConfig
db = DatabaseConfig(url="postgresql://admin:pw@db.cloud.com/logicore", pool_size=10)
```

### 3.2 `snapshot` (Tier 2 — async JSON mirror)

| Param | Type | Default | Meaning / usage |
|-------|------|---------|-----------------|
| `enabled` | `bool` | `True` | Master switch for the snapshot worker. Set `False` if you only want SQL (fastest writes, no JSON mirror). |
| `root` | `str` | `~/.logicore/snapshots` | Directory where versioned snapshot files are written (see section 5 below). |

```python
from logicore.storage import SnapshotConfig
snap = SnapshotConfig(enabled=True, root="/var/data/snapshots")
```

Each snapshot save creates two files:
- `session.json` — always the latest (overwritten on each save)
- `session_v{N}.json` — versioned copy (preserved for history)

```bash
~/.logicore/snapshots/my-session/
├── session.json        # always latest
├── session_v2.json     # revision 2
├── session_v3.json     # revision 3
└── session_v4.json     # revision 4 (latest)
```

### 3.3 `media` (Tier 3 — binary files)

| Param | Type | Default | Meaning / usage |
|-------|------|---------|-----------------|
| `root` | `str` | `~/.logicore/assets` | Where attachments go.<br>• Local path → local filesystem<br>• `s3://bucket/prefix` → cloud S3/MinIO |
| `local_storage` | `bool` | `True` | Keep local backend on. (Ignored if `root` starts with `s3://`.) |
| `max_file_size` | `int` | `100 MB` | Reject files larger than this (bytes). |

```python
from logicore.storage import MediaConfig
media = MediaConfig(root="s3://my-bucket/logicore-assets")   # cloud media
```

### 3.4 Assembling the full config

```python
from logicore.storage import StorageConfig, DatabaseConfig, SnapshotConfig, MediaConfig

config = StorageConfig(
    database=DatabaseConfig(url="postgresql://admin:pw@db.cloud.com/logicore"),
    snapshot=SnapshotConfig(enabled=True, root="/var/data/snapshots"),
    media=MediaConfig(root="s3://my-bucket/assets"),
)
```

Then: `StorageManager(config).initialize()` — or just use `create_storage()`.

---

## 4. `create_storage()` — the one-liner

Most users never touch `StorageConfig` directly. `create_storage()` builds a
sensible default and initializes it:

```python
from logicore.storage import create_storage

storage = create_storage()                 # ~/.logicore, SQLite + local files
storage = create_storage(root="/tmp/mybot")  # custom base dir
```

`create_storage(root=...)` sets `database`, `snapshot`, and `media` roots under
that folder automatically.

---

## 5. Wiring persistence into the `Agent`

The `Agent` accepts one optional parameter:

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `storage` | `StorageManager` | `None` | The persistence layer. When set, **every `chat()` auto-saves** the session + telemetry. |

```python
agent = Agent(provider="ollama", model="qwen3", storage=storage, debug=True)
```

### Session IDs are auto-generated

You no longer need to invent session IDs:

```python
sid = agent.chat("hello")          # raises? no — see note below
```

> `chat()` is `async`. In async code: `sid = await agent.chat("hello")`.
> In a sync script use `asyncio.run(agent.chat("hello"))`.
>
> If you omit `session_id`, a UUID like `session-a1b2c3d4` is created for you.
> Pass `session_id="my-id"` to reuse a conversation across restarts.

### What gets saved, and when

After each `chat()` returns, the agent calls `_persist_session()`, which:

1. Reads the in-memory session (`self.sessions[session_id]`)
2. `storage.save_session(...)` → writes to **SQL** (Tier 1)
3. Enqueues an **async snapshot** (Tier 2) — non-blocking
4. If telemetry is enabled, `storage.save_telemetry(...)` → SQL

Nothing blocks your chat loop. The snapshot worker runs in the background.

### Debug logging (watch it work)

Set `debug=True` and you'll see transaction logs:

```
[TX] save_session request sent | session=session-xxx | messages=4
[TX] save_session success | session=session-xxx | stored in SQL database | revision=5
[WORKER] queued async snapshot sync | session=session-xxx
[WORKER] syncing snapshot | session=session-xxx | reading from SQL (source of truth)
[WORKER] snapshot written to disk | session=session-xxx | path=.../session.json
```

### Session resume (same ID across restarts)

When you pass a `session_id` that already exists in storage, the agent **automatically
loads the previous conversation history** from SQL before starting the new chat turn:

```python
# Run 1 — creates the session
await agent.chat("My name is Rudra", session_id="rudra")
await agent.chat("What is 2+2?", session_id="rudra")

# Run 2 (new process) — same session ID resumes automatically
await agent.chat("What was my name?", session_id="rudra")
# Agent already knows: "Rudra"
```

What happens under the hood:
1. `get_session("rudra")` checks in-memory cache → empty (new process)
2. Loads all messages from SQL via `storage.load_session("rudra")`
3. Strips any old system message, prepends the **current** agent system prompt
4. Continues from where the conversation left off

The system prompt is always the current agent's prompt (not the stored one),
so if you updated it between runs, the new prompt takes effect immediately.

#### Exactly what gets recovered on resume

| Data | Recovered? | Where it lives |
|------|-----------|----------------|
| Conversation messages (user/assistant/tool) | ✅ Yes | Tier 1 SQL |
| Telemetry (tokens, tool calls, API calls) | ✅ Yes (in SQL) | Tier 1 SQL |
| Session metadata — `tags`, `last_tool_directory`, custom keys | ✅ Yes | Tier 1 SQL (`context` column) |
| VFS files (`session.files`) — text/JSON/images (base64) | ✅ Yes | Tier 3 `assets/{session_id}/` + ref in SQL |
| System prompt | ⚠️ Replaced by current agent's prompt | (by design, see above) |
| `created_at` / `last_activity` timestamps | ⚠️ Recreated fresh on resume | — |
| Tool approval state | ❌ Not persisted | — |
| `Agent` memory / task-manager state | ❌ Not persisted (separate systems) | — |

**VFS files** are stored as binary bytes in the Tier-3 `assets/` folder (the same
place attachments go), while a lightweight filename→path map is kept in SQL
metadata. On resume the bytes are re-read from `assets/` and rehydrated into
`session.files` verbatim — so images, PDFs, and text files survive restarts
without any special configuration.

### Snapshot versioning

Every time you save, the snapshot's **revision number increments**. Two files are
written each time:

| File | Purpose |
|------|---------|
| `session.json` | Always the latest (overwritten) |
| `session_v{N}.json` | Versioned copy (preserved forever) |

Access versioned snapshots:

```python
# Load latest
snap = storage.load_snapshot("rudra")

# Load a specific version
v2 = storage._snapshot.load_version("rudra", revision=2)

# List all versions (newest first)
versions = storage._snapshot.list_versions("rudra")
```

This lets you inspect how the conversation evolved across turns.

---

## 6. Common patterns

### 6.1 Resume a previous conversation (automatic)

Just pass the same `session_id` — the agent auto-loads from SQL:

```python
storage = create_storage()
agent = Agent(provider="ollama", model="qwen3", storage=storage)

# This resumes automatically if "my-project" exists in storage
await agent.chat("continue where we left off", session_id="my-project")
```

For manual inspection before chatting:

```python
old_msgs = storage.load_session("my-project")
if old_msgs:
    print("Previous history:", len(old_msgs), "messages")
```

### 6.2 Inspect a snapshot (read-only JSON)

```python
snap = storage.load_snapshot("my-project")
print(snap["model"], len(snap["messages"]), snap["synced_at"])
```

### 6.3 Attach a file

```python
info = storage.save_attachment("my-project", "logo", open("logo.png","rb").read(), mime="image/png")
data = storage.load_attachment(info.path)        # bytes back
```

### 6.4 Delete a session (all tiers)

```python
storage.delete_session("my-project")             # SQL + snapshot removed
```

### 6.5 Local now, cloud later

Start with the default (SQLite). To "go cloud" later, **only the config
changes** — your code stays identical:

```python
# Local (dev)
StorageConfig(database=DatabaseConfig(url="~/.logicore/db.sqlite"))

# Cloud (prod) — same API
StorageConfig(database=DatabaseConfig(url="postgresql://..."),
              media=MediaConfig(root="s3://..."))
```

---

## 7. Configuration via environment / `AgentrySettings`

You can avoid code entirely and use settings. **`.env` is auto-loaded** at
import time (`logicore/config/settings.py` calls `load_dotenv()`).

| Env var | Maps to | Example |
|---------|---------|---------|
| `LOGICORE_STORAGE_ROOT` | **base dir for ALL tiers** (deprecated alias: `STORAGE_ROOT`) | `/var/logicore` |
| `LOGICORE_STORAGE_DB_URL` | `database.url` | `postgresql://u:p@host/db` |
| `LOGICORE_STORAGE_SNAPSHOT_ENABLED` | `snapshot.enabled` | `true` / `false` |
| `LOGICORE_STORAGE_MEDIA_ROOT` | `media.root` | `s3://bucket/prefix` |

`LOGICORE_STORAGE_ROOT` is the **single global parameter** for directory setup.
Set it once and every tier (db / snapshots / assets) is created underneath it:

```bash
# .env
LOGICORE_STORAGE_ROOT=/var/logicore
```

```python
from logicore.config import settings
storage = settings.create_storage()
# -> /var/logicore/database/logicore.db
# -> /var/logicore/snapshots/<id>/session.json
# -> /var/logicore/assets/<id>/
```

All storage paths are resolved through `logicore.config` (`settings.paths`),
whether you go through `settings` or build `StorageConfig` yourself. Finer-grained
overrides use the `LOGICORE_STORAGE_*` and `LOGICORE_*_DIR` vars.

---

## 8. Crash recovery (why it's safe)

The snapshot worker is **stateless** — it always re-reads from SQL and rewrites
the full JSON. A `pending_syncs` table tracks unfinished syncs. If the process
dies mid-sync:

1. The SQL data is already safe (written before the snapshot enqueue).
2. On next `StorageManager.initialize()`, the worker sees the pending row and
   regenerates the snapshot.

No partial/corrupt snapshots ever reach disk.

---

## 9. Debugging & inspection scripts

The repo ships ready-made inspectors in `./debug/` (each enables debug logging):

```bash
python debug/00_create.py      # CREATE a session
python debug/01_read.py        # READ it back
python debug/02_update.py      # UPDATE (overwrite)
python debug/04_list.py        # LIST all sessions
python debug/05_telemetry.py   # telemetry CRUD
python debug/06_attachment.py  # binary attachment CRUD
python debug/07_snapshot.py    # inspect the JSON snapshot on disk
python debug/03_delete.py      # DELETE (run last)
```

Raw data lives at:

```
~/.logicore/
├── database/logicore.db                 # open with: sqlite3 ~/.logicore/database/logicore.db
├── snapshots/<id>/
│   ├── session.json                     # latest snapshot (always current)
│   ├── session_v2.json                  # versioned copy (revision 2)
│   ├── session_v3.json                  # versioned copy (revision 3)
│   └── session_v4.json                  # versioned copy (revision 4)
└── assets/<id>/                         # raw binary attachments
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Storage persistence failed: ...` | `storage` not initialized, or session missing | Ensure `storage.initialize()` ran and the session exists in `agent.sessions` |
| Snapshot never appears | `snapshot.enabled=False` or worker not started | Check `storage.get_worker_status()` → `running` must be `True` |
| `Object of type X is not JSON serializable` | A provider object (e.g. `ToolCall`) in messages | Already handled — `logicore/storage/json_utils.py` converts any object to JSON-safe form |
| Process hangs on Ctrl+C | Worker thread waiting to drain | `storage.shutdown()` uses short timeouts; pending syncs recover on restart |
| Want cloud but see local files | `url` not a `postgresql://` / `root` not `s3://` | Set the URL/root to the cloud scheme |
| Session not resuming across runs | `session_id` must match exactly; `storage` must be passed to Agent | Use `session_id="my-id"` and pass `storage=storage` to `Agent(...)` |
| Old system prompt shows after resume | Current system prompt is always used | Expected: the agent's current system prompt replaces the old one on resume |
| `revision` is always 1 | Old data from before revision support | Revision increments on each save going forward; old data starts at 1 |

---

## 11. Minimal end-to-end example

```python
import asyncio
from logicore import Agent
from logicore.storage import create_storage

storage = create_storage()
agent = Agent(provider="ollama", model="qwen3", storage=storage, debug=True)

async def main():
    # Run 1 — create session
    await agent.chat("Remember: my name is Rudra", session_id="user-rudra")
    await agent.chat("What is 2+2?", session_id="user-rudra")

asyncio.run(main())
storage.shutdown()
```

Re-run the same script — the agent remembers "Rudra" because the session auto-resumes:

```python
async def main():
    # Run 2 — resumes automatically
    await agent.chat("What is my name?", session_id="user-rudra")
    # Agent replies: "Rudra"
```
