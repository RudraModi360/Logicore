# Debug Scripts — Storage CRUD Inspector

Simple scripts to inspect and CRUD the 3-tier storage system.

## Prerequisites
All scripts enable `setup_debug_logging()` so you see transaction logs:
```
[TX] save_session request sent | session=... | messages=2
[TX] save_session success | session=... | stored in SQL database
[WORKER] queued async snapshot sync | session=...
[WORKER] started background snapshot worker | thread=snapshot-worker
[WORKER] syncing snapshot | session=... | reading from SQL (source of truth)
[WORKER] snapshot written to disk | session=... | path=.../session.json
```

## Usage (run in order)
```bash
python debug/00_create.py      # CREATE session
python debug/01_read.py        # READ session
python debug/02_update.py      # UPDATE (overwrite) session
python debug/04_list.py        # LIST all sessions
python debug/05_telemetry.py   # Telemetry save/read
python debug/06_attachment.py  # Binary attachment save/read/delete
python debug/07_snapshot.py    # Inspect async JSON snapshot on disk
python debug/08_supabase_diagnose.py  # Verify Supabase tables + trigger PostgREST reload
python debug/03_delete.py      # DELETE session (run last)
```

## What each script does
| Script | CRUD op | Tier |
|--------|---------|------|
| `00_create.py` | CREATE | SQL (Tier 1) |
| `01_read.py` | READ | SQL (Tier 1) |
| `02_update.py` | UPDATE | SQL (Tier 1) |
| `03_delete.py` | DELETE | SQL + Snapshot (Tier 1+2) |
| `04_list.py` | LIST | SQL (Tier 1) |
| `05_telemetry.py` | CREATE/READ | SQL (Tier 1) |
| `06_attachment.py` | CREATE/READ/DELETE | Media (Tier 3) |
| `07_snapshot.py` | INSPECT | Snapshot (Tier 2) |
| `08_supabase_diagnose.py` | DIAGNOSE | Supabase table verification + PostgREST reload |

## Jupyter Notebook (persistent connection)

`db_inspector.ipynb` — open with `jupyter notebook debug/db_inspector.ipynb`.

Connection stays alive across cells, so you can run queries without reconnect overhead. Auto-detects SQLite or PostgreSQL from `STORAGE_DB_URL` env var (or falls back to `~/.logicore/database/logicore.db`).

Built-in helpers:
- `query(sql)` → list of dicts (SELECT)
- `execute(sql)` → rowcount (INSERT/UPDATE/DELETE)
- Pre-built cells: schema, sessions, messages, telemetry, pending syncs, raw SQL runner

## Storage location
```
~/.logicore/
├── database/logicore.db     # Tier 1: SQL (canonical)
├── snapshots/<id>/session.json  # Tier 2: JSON manifest
└── assets/<id>/             # Tier 3: binary media
```
