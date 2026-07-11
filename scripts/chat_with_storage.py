"""Interactive chat with auto-persisted sessions. Ctrl+C to exit."""

import sys, os, asyncio, signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore import Agent
from logicore.storage import create_storage

storage = create_storage(
    db_url=os.environ.get("LOGICORE_DB_URL", ""),
    snapshot_enabled=True,
)
agent = Agent(provider="ollama", model="gpt-oss:20b-cloud", storage=storage, debug=True)
sid = agent.create_session()

print(f"Session: {sid}  |  Type 'quit' to exit\n")


def _cleanup(*_):
    print("\n[shutdown] Ctrl+C received, flushing storage...", flush=True)
    storage.shutdown()
    print("Session saved. Goodbye.")
    sys.exit(0)


signal.signal(signal.SIGINT, _cleanup)

try:
    while True:
        msg = input("You: ").strip()
        if msg.lower() in ("quit", "exit", ""):
            break
        try:
            resp = asyncio.run(agent.chat(msg, session_id=sid))
            print(f"AI: {resp}\n")
        except KeyboardInterrupt:
            break
except KeyboardInterrupt:
    pass
finally:
    storage.shutdown()

print("Session saved.")
