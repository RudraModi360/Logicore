import asyncio
import io
import json
import sys
import time

sys.path.insert(0, "scripts")
from openai import OpenAI

from logicore import SmartAgent

TRACE = "debug/traces/session_20261009-145709_d89f3f.jsonl"
c = OpenAI(
    api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
    base_url="https://opencode.ai/zen/v1",
    timeout=90,
)


def load_requests():
    rows = [json.loads(l) for l in io.open(TRACE, encoding="utf-8") if l.strip()]
    out = []
    for r in rows:
        if r.get("type") != "llm_request":
            continue
        msgs = []
        for m in r.get("messages") or []:
            mm = {"role": m.get("role")}
            if m.get("content") is not None:
                mm["content"] = m.get("content")
            if m.get("name"):
                mm["name"] = m.get("name")
            if m.get("tool_call_id"):
                mm["tool_call_id"] = m.get("tool_call_id")
            if m.get("tool_calls"):
                mm["tool_calls"] = m["tool_calls"]
            msgs.append(mm)
        out.append((r.get("turn"), r.get("iteration"), msgs))
    return out


async def get_tools():
    agent = SmartAgent(
        provider="custom",
        model="space-bunny-free",
        api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
        endpoint="https://opencode.ai/zen/v1",
        system_prompt="x",
        max_iterations=5,
    )
    return await agent.tool_executor.get_all_tools(agent.internal_tools, agent.disabled_tools)


def attempt(label, messages, tools=None):
    t = time.time()
    kwargs = {"model": "space-bunny-free", "messages": messages, "max_tokens": 32}
    if tools:
        kwargs["tools"] = tools
    try:
        r = c.chat.completions.create(**kwargs)
        print("%-52s OK   %.1fs prompt=%s" % (label, time.time() - t, r.usage.prompt_tokens))
        return True
    except Exception as e:
        print("%-52s ERR  %.1fs %s" % (label, time.time() - t, str(e)[:170].replace("\n", " ")))
        return False


reqs = load_requests()
t1it2 = [m for t, i, m in reqs if t == 1 and i == 2][0]
t1it3 = [m for t, i, m in reqs if t == 1 and i == 3][0]
t1it1 = [m for t, i, m in reqs if t == 1 and i == 1][0]
t2it1 = [m for t, i, m in reqs if t == 2 and i == 1][0]

tools = asyncio.run(get_tools())
print("tools:", len(tools))

print("\n--- WITH real tools array ---")
attempt("t1_it1 known-good", t1it1, tools)
attempt("t1_it2 known-good", t1it2, tools)
attempt("t1_it3 known-FAIL", t1it3, tools)
attempt("t2_it1 known-FAIL", t2it1, tools)

print("\n--- bisection of t1_it3 (with tools) ---")
attempt("drop msg3 (assistant tool_calls)", t1it3[:3] + t1it3[4:], tools)
attempt("msg3 without tool_calls field",
        t1it3[:3] + [{k: v for k, v in t1it3[3].items() if k != "tool_calls"}] + t1it3[4:], tools)
attempt("msg3 content=None", t1it3[:3] + [{"role": "assistant", "content": None, "tool_calls": t1it3[3]["tool_calls"]}] + t1it3[4:], tools)
attempt("drop msgs 4,5 (tool results)", t1it3[:4] + t1it3[6:], tools)
attempt("msgs 0-3 only", t1it3[:4], tools)
attempt("msgs 0-4 only", t1it3[:5], tools)
attempt("msgs 0-5 only", t1it3[:6], tools)
attempt("strip name fields", [{k: v for k, v in m.items() if k != "name"} for m in t1it3], tools)
attempt("strip tool_call_id fields", [{k: v for k, v in m.items() if k != "tool_call_id"} for m in t1it3], tools)
