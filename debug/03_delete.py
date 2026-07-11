"""
debug/03_delete.py — DELETE a session from all tiers (SQL + snapshot).

Run:  python debug/03_delete.py
Shows: [TX] delete_session request sent → success
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-create-001"
deleted = storage.delete_session(sid)
print(f"DELETED session: {sid} | deleted={deleted}")
storage.shutdown()
