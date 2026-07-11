"""
Crash Recovery Integration Test
================================
Tests the exact scenario the user described:
  1. User saves session (SQL commits)
  2. Window closes / process killed
  3. Snapshot worker should complete or recover on next launch

Uses subprocess with temp script files to avoid path escaping issues.
"""
import subprocess
import sys
import tempfile
import os
import shutil


def run_script(script_path: str, timeout: int = 30):
    """Run a Python script file."""
    return subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def write_script(content: str) -> str:
    """Write a script to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_normal_flow():
    """Normal flow: save -> snapshot written -> pending_syncs cleared."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db").replace("\\", "/")
    snap_path = os.path.join(tmpdir, "snapshots").replace("\\", "/")
    assets_path = os.path.join(tmpdir, "assets").replace("\\", "/")
    src_root = os.path.join(os.path.dirname(__file__), "..", "..").replace("\\", "/")

    script = f'''
import sys
sys.path.insert(0, "{src_root}")

from logicore.storage import StorageManager, StorageConfig
from logicore.storage.config import DatabaseConfig, SnapshotConfig, MediaConfig

config = StorageConfig(
    database=DatabaseConfig(url="sqlite:///{db_path}"),
    snapshot=SnapshotConfig(root="{snap_path}"),
    media=MediaConfig(root="{assets_path}"),
)
manager = StorageManager(config)
manager.initialize()

msgs = [{{"role": "user", "content": "hello world"}}]
manager.save_session("session_1", msgs, provider="openai", model="gpt-4")
manager.wait_snapshots(timeout=10.0)

snapshot = manager.load_snapshot("session_1")
assert snapshot is not None, "Snapshot should exist"
assert snapshot["session_id"] == "session_1"
assert snapshot["messages"] == msgs
assert snapshot["provider"] == "openai"

pending = manager._db.get_pending_syncs()
assert len(pending) == 0, f"pending_syncs should be empty, got: {{pending}}"

manager.close()
print("NORMAL_FLOW_PASS")
'''
    script_path = write_script(script)
    try:
        result = run_script(script_path)
        assert "NORMAL_FLOW_PASS" in result.stdout, f"Failed: stderr={result.stderr}"
        print("[PASS] Normal flow: save -> snapshot -> pending_syncs cleared")
    finally:
        os.unlink(script_path)
        shutil.rmtree(tmpdir)


def test_crash_recovery():
    """Crash flow: save -> kill process -> restart -> snapshot recovered."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db").replace("\\", "/")
    snap_path = os.path.join(tmpdir, "snapshots").replace("\\", "/")
    src_root = os.path.join(os.path.dirname(__file__), "..", "..").replace("\\", "/")

    # Step 1: Save session but close DB without letting worker finish
    script_save = f'''
import sys
sys.path.insert(0, "{src_root}")

from logicore.storage.db.sqlite import SqliteBackend
from logicore.storage.snapshot.filesystem import FilesystemSnapshotBackend

db = SqliteBackend("{db_path}")
db.initialize()
snap = FilesystemSnapshotBackend("{snap_path}")
snap.initialize()

db.create_session("crash_session", provider="anthropic", model="claude-opus")
db.save_messages("crash_session", [{{"role": "user", "content": "important message"}}])
db.save_telemetry("crash_session", input_tokens=200, output_tokens=100)
db.add_pending_sync("crash_session")

# SIMULATE CRASH: close DB immediately
db.close()
print("CRASH_SIMULATED")
'''
    script_path = write_script(script_save)
    try:
        result = run_script(script_path)
        assert "CRASH_SIMULATED" in result.stdout
        print("[STEP 1] Session saved, pending_syncs written, process 'crashed'")
    finally:
        os.unlink(script_path)

    # Verify snapshot does NOT exist yet
    snap_file = os.path.join(snap_path, "crash_session", "session.json")
    assert not os.path.exists(snap_file), "Snapshot should NOT exist yet"
    print("[STEP 2] Confirmed: snapshot does not exist yet")

    # Verify pending_syncs is in the DB
    script_check = f'''
import sys
sys.path.insert(0, "{src_root}")
from logicore.storage.db.sqlite import SqliteBackend

db = SqliteBackend("{db_path}")
db.initialize()
pending = db.get_pending_syncs()
print(f"PENDING:{{pending}}")
db.close()
'''
    script_path = write_script(script_check)
    try:
        result = run_script(script_path)
        assert "PENDING:['crash_session']" in result.stdout
        print("[STEP 3] Confirmed: pending_syncs has crash_session")
    finally:
        os.unlink(script_path)

    # Step 2: New process starts, worker recovers pending syncs
    script_recover = f'''
import sys
import time
sys.path.insert(0, "{src_root}")

from logicore.storage.db.sqlite import SqliteBackend
from logicore.storage.snapshot.filesystem import FilesystemSnapshotBackend
from logicore.storage.snapshot.worker import SnapshotWorker

db = SqliteBackend("{db_path}")
db.initialize()
snap = FilesystemSnapshotBackend("{snap_path}")
snap.initialize()

worker = SnapshotWorker(db, snap, max_retries=2, retry_delay=0.05)
worker.start()
worker.wait_drained(timeout=10.0)
time.sleep(0.5)

assert snap.snapshot_exists("crash_session"), "Snapshot should exist after recovery"
snapshot = snap.load_snapshot("crash_session")
assert snapshot["session_id"] == "crash_session"
assert snapshot["messages"] == [{{"role": "user", "content": "important message"}}]
assert snapshot["provider"] == "anthropic"
assert snapshot["model"] == "claude-opus"
assert snapshot["telemetry"]["input_tokens"] == 200
assert snapshot["telemetry"]["output_tokens"] == 100
assert snapshot["telemetry"]["total_tokens"] == 300

pending = db.get_pending_syncs()
assert len(pending) == 0, f"pending_syncs should be empty, got: {{pending}}"

worker.stop()
db.close()
print("RECOVERY_PASS")
'''
    script_path = write_script(script_recover)
    try:
        result = run_script(script_path)
        assert "RECOVERY_PASS" in result.stdout, f"Failed: {result.stderr}"
        print("[STEP 4] Snapshot recovered successfully after simulated crash")
        print("[STEP 5] pending_syncs cleared after recovery")
        print("[PASS] Crash recovery flow works end-to-end")
    finally:
        os.unlink(script_path)
        shutil.rmtree(tmpdir)


def test_partial_crash_recovery():
    """Partial crash: 3 sessions saved, only 2 synced before crash."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db").replace("\\", "/")
    snap_path = os.path.join(tmpdir, "snapshots").replace("\\", "/")
    src_root = os.path.join(os.path.dirname(__file__), "..", "..").replace("\\", "/")

    # Step 1: Create 3 sessions, sync 2, crash on 3rd
    script_partial = f'''
import sys
import os
import json
from datetime import datetime
sys.path.insert(0, "{src_root}")

from logicore.storage.db.sqlite import SqliteBackend

db = SqliteBackend("{db_path}")
db.initialize()

for i in range(3):
    db.create_session(f"sess_{{i}}", provider="openai", model="gpt-4")
    db.save_messages(f"sess_{{i}}", [{{"role": "user", "content": f"msg {{i}}"}}])

db.add_pending_sync("sess_0")
db.add_pending_sync("sess_1")
db.add_pending_sync("sess_2")

# Manually write snapshots for sess_0 and sess_1 only
for i in range(2):
    sid = f"sess_{{i}}"
    manifest = {{"session_id": sid, "messages": [{{"role": "user", "content": f"msg {{i}}"}}], "synced_at": datetime.now().isoformat()}}
    snap_dir = os.path.join("{snap_path}", sid)
    os.makedirs(snap_dir, exist_ok=True)
    with open(os.path.join(snap_dir, "session.json"), "w") as f:
        json.dump(manifest, f)
    db.remove_pending_sync(sid)

pending = db.get_pending_syncs()
assert pending == ["sess_2"], f"Expected only sess_2 pending, got: {{pending}}"
db.close()
print("PARTIAL_CRASH_SIMULATED")
'''
    script_path = write_script(script_partial)
    try:
        result = run_script(script_path)
        assert "PARTIAL_CRASH_SIMULATED" in result.stdout
        print("[STEP 1] Partial crash simulated: sess_0,1 synced; sess_2 pending")
    finally:
        os.unlink(script_path)

    # Step 2: New process recovers sess_2
    script_recover = f'''
import sys
import time
sys.path.insert(0, "{src_root}")

from logicore.storage.db.sqlite import SqliteBackend
from logicore.storage.snapshot.filesystem import FilesystemSnapshotBackend
from logicore.storage.snapshot.worker import SnapshotWorker

db = SqliteBackend("{db_path}")
db.initialize()
snap = FilesystemSnapshotBackend("{snap_path}")
snap.initialize()

worker = SnapshotWorker(db, snap, max_retries=2, retry_delay=0.05)
worker.start()
worker.wait_drained(timeout=10.0)
time.sleep(0.5)

assert snap.snapshot_exists("sess_2"), "sess_2 snapshot should exist after recovery"
snapshot = snap.load_snapshot("sess_2")
assert snapshot["session_id"] == "sess_2"
assert snapshot["messages"] == [{{"role": "user", "content": "msg 2"}}]

for i in range(3):
    assert snap.snapshot_exists(f"sess_{{i}}"), f"sess_{{i}} should exist"

pending = db.get_pending_syncs()
assert len(pending) == 0

worker.stop()
db.close()
print("PARTIAL_RECOVERY_PASS")
'''
    script_path = write_script(script_recover)
    try:
        result = run_script(script_path)
        assert "PARTIAL_RECOVERY_PASS" in result.stdout, f"Failed: {result.stderr}"
        print("[STEP 2] sess_2 recovered after partial crash")
        print("[STEP 3] All 3 snapshots exist, pending_syncs cleared")
        print("[PASS] Partial crash recovery works")
    finally:
        os.unlink(script_path)
        shutil.rmtree(tmpdir)


def test_worker_survives_across_instances():
    """Worker in process A enqueues, process B (new) recovers."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db").replace("\\", "/")
    snap_path = os.path.join(tmpdir, "snapshots").replace("\\", "/")
    assets_path = os.path.join(tmpdir, "assets").replace("\\", "/")
    src_root = os.path.join(os.path.dirname(__file__), "..", "..").replace("\\", "/")

    # Process A: Save and exit immediately
    script_a = f'''
import sys
sys.path.insert(0, "{src_root}")

from logicore.storage.db.sqlite import SqliteBackend

db = SqliteBackend("{db_path}")
db.initialize()

for i in range(5):
    db.create_session(f"work_{{i}}", provider="ollama", model="llama3")
    db.save_messages(f"work_{{i}}", [{{"role": "user", "content": f"work item {{i}}"}}])
    db.add_pending_sync(f"work_{{i}}")

db.close()
print("PROCESS_A_DONE")
'''
    script_path = write_script(script_a)
    try:
        result = run_script(script_path)
        assert "PROCESS_A_DONE" in result.stdout
        print("[STEP 1] Process A: saved 5 sessions, all pending")
    finally:
        os.unlink(script_path)

    # Process B: Start fresh, recover all
    script_b = f'''
import sys
import time
sys.path.insert(0, "{src_root}")

from logicore.storage import StorageManager, StorageConfig
from logicore.storage.config import DatabaseConfig, SnapshotConfig, MediaConfig

config = StorageConfig(
    database=DatabaseConfig(url="sqlite:///{db_path}"),
    snapshot=SnapshotConfig(root="{snap_path}"),
    media=MediaConfig(root="{assets_path}"),
)
manager = StorageManager(config)
manager.initialize()

manager.wait_snapshots(timeout=10.0)
time.sleep(0.5)

for i in range(5):
    sid = f"work_{{i}}"
    snapshot = manager.load_snapshot(sid)
    assert snapshot is not None, f"{{sid}} snapshot should exist"
    assert snapshot["session_id"] == sid
    assert snapshot["messages"] == [{{"role": "user", "content": f"work item {{i}}"}}]
    assert snapshot["provider"] == "ollama"
    assert snapshot["model"] == "llama3"

pending = manager._db.get_pending_syncs()
assert len(pending) == 0, f"pending_syncs should be empty, got: {{pending}}"

manager.close()
print("PROCESS_B_DONE")
'''
    script_path = write_script(script_b)
    try:
        result = run_script(script_path)
        assert "PROCESS_B_DONE" in result.stdout, f"Failed: {result.stderr}"
        print("[STEP 2] Process B: recovered all 5 snapshots")
        print("[STEP 3] All pending_syncs cleared")
        print("[PASS] Worker recovery across process instances works")
    finally:
        os.unlink(script_path)
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    print("=" * 60)
    print("CRASH RECOVERY INTEGRATION TESTS")
    print("=" * 60)
    print()
    test_normal_flow()
    print()
    test_crash_recovery()
    print()
    test_partial_crash_recovery()
    print()
    test_worker_survives_across_instances()
    print()
    print("=" * 60)
    print("ALL CRASH RECOVERY TESTS PASSED")
    print("=" * 60)
