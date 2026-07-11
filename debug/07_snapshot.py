"""
debug/07_snapshot.py — INSPECT the async snapshot (JSON manifest on disk).

Run:  python debug/07_snapshot.py
Shows: [WORKER] syncing snapshot → written to disk
       Manifest contents (messages, model, tokens)
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-snapshot-001"
storage.save_session(sid, [
    {"role": "user", "content": "inspect my snapshot"},
    {"role": "assistant", "content": "Check the JSON manifest on disk!"},
], provider="ollama", model="qwen3")

# Wait for async worker to flush
storage.wait_snapshots(timeout=5)

snap = storage.load_snapshot(sid)
if snap:
    print(f"SNAPSHOT for {sid}:")
    print(f"  format={snap.get('format')} | version={snap.get('version')}")
    print(f"  model={snap.get('model')} | messages={len(snap.get('messages', []))}")
    print(f"  synced_at={snap.get('synced_at')}")
else:
    print(f"No snapshot found for {sid}")
storage.shutdown()
