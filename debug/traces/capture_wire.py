import asyncio
import io
import json
import os
import sys
import time

sys.path.insert(0, "scripts")
from logicore import SmartAgent

OUT = "debug/traces/wire"
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    os.remove(os.path.join(OUT, f))

calls = []


def make_wrapper(client, index_file):
    original = client.chat.completions.create

    def wrapper(*args, **kwargs):
        idx = len(calls)
        rec = {"idx": idx, "t": time.time(), "kwargs": kwargs}
        calls.append(rec)
        with io.open(os.path.join(OUT, "req_%02d.json" % idx), "w", encoding="utf-8") as f:
            json.dump(rec, f, default=str, ensure_ascii=False)
        t = time.time()
        try:
            resp = original(*args, **kwargs)
            calls[-1]["ok"] = True
            calls[-1]["dt"] = time.time() - t
            try:
                calls[-1]["usage"] = str(resp.usage)
            except Exception:
                pass
            with io.open(index_file, "w", encoding="utf-8") as f:
                json.dump(calls, f, default=str, ensure_ascii=False)
            return resp
        except Exception as e:
            calls[-1]["ok"] = False
            calls[-1]["dt"] = time.time() - t
            calls[-1]["err"] = str(e)[:300]
            with io.open(index_file, "w", encoding="utf-8") as f:
                json.dump(calls, f, default=str, ensure_ascii=False)
            raise

    return wrapper


async def main():
    agent = SmartAgent(
        provider="custom",
        model="space-bunny-free",
        api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
        endpoint="https://opencode.ai/zen/v1",
        system_prompt="You are a terse assistant. Use the list_files tool once, then answer 'done'.",
        debug=False,
        telemetry=False,
        max_iterations=6,
    )
    agent.set_allow_tools({"list_files", "read_file", "web_search", "url_fetch"})
    client = agent.provider.client
    client.chat.completions.create = make_wrapper(client, os.path.join(OUT, "index.json"))
    t0 = time.time()
    try:
        result = await agent._chat_orchestrator.run(
            user_input="list the files in the current directory, then reply with just: done",
            session_id="wire-cap",
        )
    except Exception:
        import traceback
        traceback.print_exc()
        result = None
    print("elapsed %.1fs" % (time.time() - t0))
    print("result:", str(result)[:300])
    print("\nlive calls:")
    for c in calls:
        kw = c["kwargs"]
        print("  #%d ok=%s dt=%.1fs messages=%d tools=%s stream=%s usage=%s err=%s" % (
            c["idx"], c.get("ok"), c.get("dt", -1), len(kw.get("messages", [])),
            len(kw.get("tools") or []), kw.get("stream"), c.get("usage"), (c.get("err") or "")[:80]))


asyncio.run(main())
