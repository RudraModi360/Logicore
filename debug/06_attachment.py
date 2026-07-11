"""
debug/06_attachment.py — Attachment CRUD (save binary + read + delete).

Run:  python debug/06_attachment.py
Shows: [TX] save_attachment request sent → success | sha256
       [TX] load_attachment (binary bytes)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore.logging_setup import setup_debug_logging
setup_debug_logging()

from logicore.storage import create_storage

storage = create_storage()
sid = "debug-attach-001"
storage.save_session(sid, [{"role": "user", "content": "send me a file"}], provider="ollama", model="qwen3")

# Save a fake PNG attachment
fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
info = storage.save_attachment(sid, "logo", fake_png, mime="image/png")
print(f"SAVED attachment | path={info.path} | sha256={info.sha256[:12]}...")

# Read it back
data = storage.load_attachment(info.path)
print(f"READ attachment | {len(data)} bytes | match={data == fake_png}")

ok = storage.delete_attachment(info.path)
print(f"DELETED attachment | ok={ok}")
storage.shutdown()
