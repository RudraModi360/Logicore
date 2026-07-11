"""
debug/04_list.py — LIST all sessions in the SQL database.

Run:  python debug/04_list.py
Shows: [TX] list_sessions request sent → success
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sessions = storage.list_sessions()
print(f"LIST | {len(sessions)} sessions:")
for s in sessions:
    print(f"  {s['session_id']} | {s.get('provider', '')}/{s.get('model', '')} | msgs={s.get('message_count', '?')}")
storage.shutdown()
