import asyncio
import io
import json
import sys
import time

sys.path.insert(0, "scripts")
from openai import OpenAI

from logicore import SmartAgent
from logicore.gateway.base import _serialize_tool_call_arguments

TRACE = "debug/traces/session_20261009-145709_d89f3f.jsonl"
c = OpenAI(
    api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
    base_url="https://opencode.ai/zen/v1",
    timeout=90,
)


def load():
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
        api_key="x",
        endpoint="https://opencode.ai/zen/v1",
        system_prompt="x",
        max_iterations=5,
    )
    return await agent.tool_executor.get_all_tools(agent.internal_tools, agent.disabled_tools)


def attempt(label, messages, tools=None):
    wire = _serialize_tool_call_arguments(list(messages))
    payload = {"model": "space-bunny-free", "messages": wire}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    t = time.time()
    try:
        r = c.chat.completions.create(**payload)
        print("%-56s OK   %.1fs prompt=%s" % (label, time.time() - t, r.usage.prompt_tokens))
        return True
    except Exception as e:
        print("%-56s ERR  %.1fs %s" % (label, time.time() - t, str(e)[:150].replace("\n", " ")))
        return False


reqs = load()
t1it1 = [m for t, i, m in reqs if t == 1 and i == 1][0]
t1it2 = [m for t, i, m in reqs if t == 1 and i == 2][0]
t1it3 = [m for t, i, m in reqs if t == 1 and i == 3][0]
t2it1 = [m for t, i, m in reqs if t == 2 and i == 1][0]
tools = asyncio.run(get_tools())
print("tools:", len(tools))

print("\n--- with gateway serialization (the true wire shape) ---")
attempt("t1_it1 known-good", t1it1, tools)
attempt("t1_it2 known-good", t1it2, tools)
attempt("t1_it3 known-FAIL", t1it3, tools)
attempt("t2_it1 known-FAIL", t2it1, tools)

print("\n--- t1_it3 surgery: the stray system msg at index 7 ---")
attempt("drop msg7 (stray system)", t1it3[:7] + t1it3[8:], tools)
attempt("move msg7 to the very end", t1it3[:7] + t1it3[8:] + t1it3[7:8], tools)
attempt("keep msg7 but after tool msgs (swap 7<->8)",
        t1it3[:7] + [t1it3[8], t1it3[9], t1it3[7]], tools)

print("\n--- synthetic: does ANY system msg between assistant(tc) and tool msgs break it? ---")
hint = {"role": "system", "content": "hint: prefer other tools"}
attempt("control: t1it2 clean (known good)", t1it2, tools)
attempt("t1it2 + system hint inserted between asst(tc) and tools",
        t1it2[:4] + [hint] + t1it2[4:], tools)
attempt("t1it2 + same hint appended at end", t1it2 + [hint], tools)

print("\n--- t2_it1 surgery ---")
attempt("t2_it1 drop all system msgs after idx1",
        [m for i, m in enumerate(t2it1) if not (i > 1 and m["role"] == "system")], tools)
