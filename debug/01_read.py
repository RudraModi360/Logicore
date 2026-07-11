"""
debug/01_read.py — READ a session from the SQL database.

Run:  python debug/01_read.py
Shows: [TX] load_session request sent → success
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-create-001"
msgs = storage.load_session(sid)
if msgs:
    print(f"READ session: {sid} | {len(msgs)} messages")
    for m in msgs:
        print(f"  [{m['role']}] {m['content'][:60]}")
else:
    print(f"Session {sid} not found. Run 00_create.py first.")
storage.shutdown()
