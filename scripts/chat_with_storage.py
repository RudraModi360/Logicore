"""Interactive chat with auto-persisted sessions. Ctrl+C to exit."""

import sys, os, asyncio, signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore import Agent
from logicore.storage import create_storage

storage = create_storage(
    db_url=os.environ.get("LOGICORE_DB_URL", ""),
    snapshot_enabled=True,
)
agent = Agent(provider="ollama", model="gpt-oss:20b-cloud", storage=storage, debug=True, telemetry=True)
sid = agent.create_session(session_id='session-70a9a4f8')

print(f"Session: {sid}  |  Type 'quit' to exit\n")


def _print_usage(agent):
    u = agent.usage
    if not u or u.get("api_calls", 0) == 0:
        return
    inp = u["input_tokens"]
    out = u["output_tokens"]
    cr = u["cache_read_tokens"]
    cw = u["cache_write_tokens"]
    reasoning = u["reasoning_tokens"]
    total = u["total_tokens"]
    api_calls = u["api_calls"]
    cost = u.get("estimated_cost_usd", 0)
    status = u.get("cost_status", "unknown")

    parts = [f"in={inp}", f"out={out}"]
    if cr:
        parts.append(f"cache_r={cr}")
    if cw:
        parts.append(f"cache_w={cw}")
    if reasoning:
        parts.append(f"reason={reasoning}")
    parts.append(f"total={total}")
    parts.append(f"calls={api_calls}")
    if cost and cost > 0:
        parts.append(f"cost=${cost:.4f}({status})")
    elif status == "included":
        parts.append("cost=included")
    print(f"  [{', '.join(parts)}]")


def _cleanup(*_):
    print("\n[shutdown] Ctrl+C received, flushing storage...", flush=True)
    _print_usage(agent)
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
            _print_usage(agent)
        except KeyboardInterrupt:
            break
except KeyboardInterrupt:
    pass
finally:
    storage.shutdown()

print("Session saved.")
