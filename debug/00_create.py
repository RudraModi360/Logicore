"""
debug/00_create.py — CREATE a session in the SQL database.

Run:  python debug/00_create.py
Shows: [TX] save_session request sent → success
       [WORKER] queued async snapshot sync
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()  # enables [TX] / [WORKER] debug logs

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-create-001"
storage.save_session(sid, [
    {"role": "user", "content": "Hello from debug create"},
    {"role": "assistant", "content": "Session stored in SQL database!"},
], provider="ollama", model="qwen3")
print(f"CRETED session: {sid}")
storage.shutdown()
