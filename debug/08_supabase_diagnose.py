"""Diagnose + fix: ensure Supabase tables are visible in dashboard.

Usage:
    set STORAGE_DB_URL=postgresql://postgres.xxx:PASSWORD@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres
    python debug/08_supabase_diagnose.py
"""
import os, sys, socket
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

DB_URL = os.getenv("STORAGE_DB_URL", "")
if not DB_URL:
    try:
        from logicore.config import settings
        DB_URL = settings.STORAGE_DB_URL
    except Exception:
        pass

if not DB_URL or not DB_URL.startswith(("postgresql://", "postgres://")):
    print("ERROR: Set STORAGE_DB_URL to a PostgreSQL connection string")
    sys.exit(1)

from urllib.parse import urlparse
parsed = urlparse(DB_URL)
hostname = parsed.hostname
port = parsed.port or 5432

print(f"Host: {hostname}:{port}")
print()

# ── DNS check ──────────────────────────────────────────────────────
print("=== DNS ===")
try:
    ip = socket.gethostbyname(hostname)
    print(f"  OK — {hostname} -> {ip}")
except socket.gaierror as e:
    print(f"  FAIL — {e}")
    print("  Use the POOLER connection URL from Supabase Dashboard → Settings → Database")
    sys.exit(1)

# ── Connect ────────────────────────────────────────────────────────
print("\n=== Connect ===")
try:
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    print("  OK")
except Exception as e:
    print(f"  FAIL — {e}")
    sys.exit(1)

# ── List existing tables ───────────────────────────────────────────
print("\n=== Tables in public schema ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [r[0] for r in cur.fetchall()]
    if tables:
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            count = cur.fetchone()[0]
            print(f"  {t:30s}  {count} rows")
    else:
        print("  (none)")

# ── Create tables if missing ───────────────────────────────────────
needed = {"sessions", "session_telemetry", "pending_syncs", "schema_version"}
missing = needed - set(tables)
if missing:
    print(f"\n=== Creating missing tables: {sorted(missing)} ===")
    with conn.cursor() as cur:
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")
        cur.execute("SELECT COUNT(*) FROM schema_version")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO schema_version (version) VALUES (1)")
    print("  Tables created.")

# ── Verify tables exist now ────────────────────────────────────────
print("\n=== Verify ===")
with conn.cursor() as cur:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [r[0] for r in cur.fetchall()]
    print(f"  Tables: {tables}")

# ── PostgREST reload ───────────────────────────────────────────────
print("\n=== PostgREST Reload ===")
try:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_notify('pgrst', 'reload schema')")
    print("  NOTIFY sent via pooler connection")
except Exception as e:
    print(f"  NOTIFY failed: {e}")

conn.close()

print()
print("=" * 60)
print("IF TABLES STILL DON'T APPEAR IN DASHBOARD:")
print()
print("  1. Go to Supabase Dashboard → SQL Editor")
print("  2. Run this EXACT command:")
print()
print("     NOTIFY pgrst, 'reload schema';")
print()
print("  3. Wait 5 seconds, then refresh the Table Editor page")
print("=" * 60)
