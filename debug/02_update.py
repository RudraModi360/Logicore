"""
debug/02_update.py — UPDATE a session (overwrite messages + metadata).

Run:  python debug/02_update.py
Shows: [TX] save_session request sent → success
       [WORKER] queued async snapshot sync
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-create-001"

# Update = save again with same session_id (overwrites messages)
storage.save_session(sid, [
    {"role": "user", "content": "Hello from debug create"},
    {"role": "assistant", "content": "Session stored in SQL database!"},
    {"role": "user", "content": "Now updating this session"},
    {"role": "assistant", "content": "Updated! Messages overwritten."},
], provider="ollama", model="qwen3", description="updated via debug")

print(f"UPDATED session: {sid}")
storage.shutdown()
