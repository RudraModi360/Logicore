"""
debug/05_telemetry.py — Telemetry CRUD (save + read + summary).

Run:  python debug/05_telemetry.py
Shows: [TX] save_telemetry request sent → success
       [TX] load_session / list for summary
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "session-afec014e"
storage.save_session(sid, [{"role": "user", "content": "hi"}], provider="ollama", model="qwen3")

storage.save_telemetry(sid, input_tokens=500, output_tokens=300, cache_read_tokens=100, tool_calls=4)
print(f"SAVED telemetry for: {sid}")

tel = storage.load_telemetry(sid)
print(f"RAW telemetry: {tel}")

summary = storage.get_telemetry_summary(sid)
print(f"SUMMARY: total_tokens={summary['total_tokens']}")
storage.shutdown()
