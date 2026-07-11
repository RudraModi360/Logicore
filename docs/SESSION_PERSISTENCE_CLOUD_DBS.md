# Cloud Database Setup Guide — Logicore Session Persistence

This guide explains how to point Logicore's 3-tier session persistence at a
**managed PostgreSQL cloud database** (the only protocol the codebase supports
out of the box today). It is written for the operator / DevOps persona: "I have
a cloud DB, where do I get the credentials, where do I put them, and what
happens internally."

> **TL;DR** — Every managed **PostgreSQL** database works with zero code
> changes. Set two environment variables, restart, done. The only supported
> protocol today is PostgreSQL (and local SQLite for dev). Non-PostgreSQL DBs
> (MySQL, Turso/libSQL, Cloudflare D1) require a custom backend — see
> `SESSION_PERSISTENCE_BACKEND_EXTENSION.md`.

---

## 1. How the system picks a backend

In `StorageManager.initialize()` the database backend is selected purely from
the URL scheme:

| URL scheme | Backend used | Driver |
|------------|--------------|--------|
| `postgresql://` or `postgres://` | `PostgresBackend` | `psycopg2` |
| `sqlite:///...` (or unset) | `SqliteBackend` | stdlib `sqlite3` |

So **any** database reachable via a `postgresql://` connection string is handled
by `PostgresBackend`. The vendor (Supabase, Neon, RDS, …) does not matter — only
the wire protocol does.

---

## 2. Prerequisites

The PostgreSQL backend uses `psycopg2`. It is already a dependency, but if you
hit `ModuleNotFoundError: No module named 'psycopg2'`, install the binary build:

```bash
pip install "psycopg2-binary>=2.9"
```

For serverless / pooled connections most providers also work with the pure
Python driver `psycopg` (v3). `psycopg2` is what the backend imports today.

---

## 3. The two environment variables you need

| Env var | Purpose | Required |
|---------|---------|----------|
| `LOGICORE_STORAGE_DB_URL` | Full `postgresql://` connection string | **Yes** |
| `LOGICORE_STORAGE_DB_PASSWORD` | Password (only if **not** already embedded in the URL) | Optional* |

\* Most providers include the password in the connection string they hand you,
so you only need `LOGICORE_STORAGE_DB_PASSWORD` if you deliberately split it
out. If the password is in the URL, leave this unset (it defaults to `""`).

Optional companion vars (media tier — covered at the end):

| Env var | Purpose |
|---------|---------|
| `LOGICORE_STORAGE_MEDIA_ROOT` | `s3://bucket/prefix` to store binary media in cloud object storage |
| `LOGICORE_STORAGE_S3_ENDPOINT` | S3-compatible endpoint (e.g. Supabase Storage) |
| `LOGICORE_STORAGE_S3_ACCESS_KEY` | S3 access key |
| `LOGICORE_STORAGE_S3_SECRET_KEY` | S3 secret key |
| `LOGICORE_STORAGE_S3_REGION` | S3 region |
| `LOGICORE_STORAGE_SNAPSHOT_ENABLED` | `true`/`false` — toggle the async JSON snapshot tier |

---

## 4. Per-database credential recovery

Each section below: **where to click**, **what string you copy**, and **the
`.env` line** you write. Replace placeholders in `<angle brackets>`.

### 4.1 Supabase

1. Open the project dashboard → **Project Settings → Database**.
2. In **Connection string**, switch to the **Python** tab.
3. Copy the string. For serverless / pooled connections use the **Transaction
   pooler** (port **6543**) rather than the direct connection (port 5432), which
   can fail DNS/connection limits under load:

   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

   If you prefer the direct connection (port 5432):

   ```
   postgresql://postgres.<project-ref>:<password>@db.<project-ref>.supabase.co:5432/postgres
   ```
4. `.env`:

   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

   > The password is embedded in the URL above, so `LOGICORE_STORAGE_DB_PASSWORD`
   > is **not** needed.
5. **One extra step for Supabase only:** if you create the tables via the SQL
   Editor (outside the normal startup lifecycle) and they don't appear in the
   table view, the PostgREST schema cache needs a nudge. The backend calls
   `reload_schema()` automatically on init; you can also force it:
   ```python
   from logicore.storage import StorageManager
   manager.reload_schema()   # sends NOTIFY pgrst, 'reload schema'
   ```
   On **every other** PostgreSQL provider this call is a no-op (see §5).

### 4.2 Neon

1. Dashboard → your project → **Connection Details**.
2. Choose **Python**, pick the branch and the **pooled** toggle if desired.
3. Copy the string (it already contains `?sslmode=require`):
   ```
   postgresql://<user>:<password>@<ep-xxxx>.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@<ep-xxxx>.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   `psycopg2` honours `sslmode` from the query string. No extra step needed.

### 4.3 Render (PostgreSQL)

1. Dashboard → your **PostgreSQL** service → **Connect** tab.
2. Copy **External Database URL** (or Internal if both services are on Render):
   ```
   postgresql://<user>:<password>@<db-host>.render.com:5432/<dbname>
   ```
3. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@<db-host>.render.com:5432/<dbname>
   ```

### 4.4 AWS RDS for PostgreSQL

1. RDS console → your instance → **Connectivity & security** → copy the
   **Endpoint** (e.g. `mydb.123456.us-east-1.rds.amazonaws.com`) and **Port**
   (default `5432`).
2. Use the master username/password you set at creation (or a dedicated IAM /
   least-privilege role).
3. Ensure the instance's **security group** allows inbound `5432` from your
   app's IP / VPC.
4. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@<endpoint>:5432/<dbname>?sslmode=require
   ```
   SSL is optional on RDS but recommended; the `sslmode=require` query param is
   sufficient for `psycopg2`.

### 4.5 Google Cloud SQL for PostgreSQL

Two common access patterns:

**A. Public IP + SSL (simplest):**
1. Console → **SQL → your instance → Connections** → enable **Public IP**, add
   your app's IP to authorized networks.
2. Download the server CA cert if you want `verify-full`; `require` works with
   just the connection string.
3. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@<public-ip>:5432/<dbname>?sslmode=require
   ```

**B. Cloud SQL Auth Proxy (recommended for GCP workloads):**
1. Run the proxy: `./cloud-sql-proxy <project>:us-central1:<instance>`
2. It exposes `localhost:5432` over a secure tunnel (no password in transit).
3. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@127.0.0.1:5432/<dbname>
   ```

### 4.6 Azure Database for PostgreSQL

1. Portal → your server → **Connection strings** → **ADO.NET / PostgreSQL**.
2. Azure requires SSL and a `user@server` username format:
   ```
   postgresql://<user>@<server>:<password>@<server>.postgres.database.azure.com:5432/<dbname>?sslmode=require
   ```
3. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>@<server>:<password>@<server>.postgres.database.azure.com:5432/<dbname>?sslmode=require
   ```
   > Note the doubled `@` — Azure's username embeds the server name; the password
   > sits between the two `@` signs. Keep it exactly as the portal shows.

### 4.7 CockroachDB (Serverless / Dedicated)

1. Console → your cluster → **Connect → General connection string → Python**.
2. CockroachDB speaks the PostgreSQL wire protocol; the string carries an SSL
   root cert reference:
   ```
   postgresql://<user>:<password>@<cluster>.cockroachlabs.cloud:26257/<dbname>?sslmode=verify-full&sslrootcert=<path>/cc-ca.crt
   ```
3. `.env`:
   ```dotenv
   LOGICORE_STORAGE_DB_URL=postgresql://<user>:<password>@<cluster>.cockroachlabs.cloud:26257/<dbname>?sslmode=verify-full&sslrootcert=<path>/cc-ca.crt
   ```
   > Caveat: CockroachDB is PostgreSQL-*compatible* but not 100% identical. The
   > schema uses `JSONB` and `RETURNING`, both supported. If you hit a dialect
   > quirk, see the extension guide to subclass `PostgresBackend` and override
   > the offending statement.

---

## 5. What happens internally (shared across all PostgreSQL providers)

When `LOGICORE_STORAGE_DB_URL` starts with `postgresql://`/`postgres://`:

1. `StorageManager.initialize()` instantiates `PostgresBackend(url, password, pool_size)`.
2. `PostgresBackend.initialize()` runs `CREATE TABLE IF NOT EXISTS` for:
   - `sessions` — one row per session (provider, model, description, revision
     counter, `context` JSONB holding `tags`/`last_tool_directory`/`_vfs_files`).
   - `messages` — message list stored as a single `JSONB` column (keyed by
     `session_id`).
   - `telemetry` — optional usage/telemetry rows.
   - `pending_syncs` — queue of sessions awaiting snapshot sync.
   - `schema_version` — single-row table tracking migration level.
3. Every write bumps the per-session **revision** counter (`increment_revision`)
   via `UPDATE ... RETURNING revision` for optimistic consistency.
4. On `StorageManager.reload_schema()`:
   - **Supabase hosts only** (`*.supabase.co` / `*.supabase.in`): fires
     `pg_notify('pgrst', 'reload schema')` so the dashboard's PostgREST cache
     picks up the new tables.
   - **All other hosts**: does nothing (plain PostgreSQL has no PostgREST to
     notify). This is why Neon/RDS/Azure/etc. need no special step.
5. `load_session()` reconstructs messages, `metadata` (from `context`), and VFS
   `files` (rehydrated from the `assets/` media tier) — see
   `SESSION_PERSISTENCE_USER_GUIDE.md`.

---

## 6. Verify the connection

After setting the env vars and starting the agent, confirm tables exist and a
round-trip works:

```python
from logicore.storage import StorageManager, StorageConfig

mgr = StorageManager(StorageConfig())
mgr.initialize()

# Write + read back
mgr.save_session("verify-1", messages=[{"role": "user", "content": "hi"}],
                 provider="openai", model="gpt-4")
assert mgr.load_session("verify-1") == [{"role": "user", "content": "hi"}]
print("OK — cloud DB connected")
```

Or use the Jupyter inspector: `debug/db_inspector.ipynb` (auto-detects
SQLite/PostgreSQL and lists all tables).

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError: psycopg2` | driver missing | `pip install psycopg2-binary` |
| `could not translate host name` / DNS error | using Supabase **direct** host under load | switch to the **pooler** URL (port 6543) |
| `password authentication failed` | wrong creds / split password | put password in URL **or** set `LOGICORE_STORAGE_DB_PASSWORD` |
| `SSL required` (RDS/Azure) | `sslmode` missing | append `?sslmode=require` to the URL |
| Tables missing in Supabase **dashboard** only | PostgREST cache | `mgr.reload_schema()` or run `NOTIFY pgrst, 'reload schema'` in SQL Editor |
| `NoSuchBucket` (media) | S3 bucket not created | create the bucket in the storage console, then re-run |

---

## 8. Pairing with cloud media (optional)

If you also want binary attachments (images, files) in the cloud instead of
local disk, set the media tier to an S3-compatible bucket:

```dotenv
LOGICORE_STORAGE_MEDIA_ROOT=s3://<bucket>/<prefix>
LOGICORE_STORAGE_S3_ENDPOINT=https://<project-ref>.supabase.co/storage/v1/s3
LOGICORE_STORAGE_S3_ACCESS_KEY=<access-key>
LOGICORE_STORAGE_S3_SECRET_KEY=<secret-key>
LOGICORE_STORAGE_S3_REGION=<region>
```

The bucket **must already exist** (S3 APIs cannot auto-create it). See
`SESSION_PERSISTENCE_USER_GUIDE.md` for the full media-tier explanation.
