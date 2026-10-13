import json
import io
import time
from openai import OpenAI

TRACE = "debug/traces/session_20261009-145709_d89f3f.jsonl"
c = OpenAI(
    api_key="oc_sk_4c01c67f0b88_tcNDptmcoTcfpjkEouhfrzwMZa-Vq5Z9",
    base_url="https://opencode.ai/zen/v1",
    timeout=90,
)

rows = [json.loads(l) for l in io.open(TRACE, encoding="utf-8") if l.strip()]

requests = []
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
    requests.append((r.get("turn"), r.get("iteration"), msgs))

print("captured requests:", [(t, i, len(m)) for t, i, m in requests])


def attempt(label, messages):
    t = time.time()
    try:
        r = c.chat.completions.create(
            model="space-bunny-free", messages=messages, max_tokens=32
        )
        print("%-46s OK   %.1fs prompt=%s" % (label, time.time() - t, r.usage.prompt_tokens))
        return True
    except Exception as e:
        print("%-46s ERR  %.1fs %s" % (label, time.time() - t, str(e)[:160].replace("\n", " ")))
        return False


turn1_it3 = [m for t, i, m in requests if t == 1 and i == 3][0]
turn1_it2 = [m for t, i, m in requests if t == 1 and i == 2][0]
turn1_it1 = [m for t, i, m in requests if t == 1 and i == 1][0]
turn2_it1 = [m for t, i, m in requests if t == 2 and i == 1][0]

print("\n--- replay exact payloads (no tools) ---")
attempt("t1_it1 (known good)", turn1_it1)
attempt("t1_it2 (known good)", turn1_it2)
attempt("t1_it3 (known FAIL)", turn1_it3)
attempt("t2_it1 (known FAIL)", turn2_it1)

print("\n--- bisect t1_it3: drop messages from the end ---")
for keep in range(len(turn1_it3), 0, -1):
    if attempt("t1_it3 keep first %d msgs" % keep, turn1_it3[:keep]):
        break

print("\n--- bisect t1_it3: drop the two leading system msgs ---")
attempt("t1_it3 without msg0 (sys 262)", turn1_it3[1:])
attempt("t1_it3 only sys2 + user", [turn1_it3[1], turn1_it3[2]])
attempt("t1_it3 content only (no tool fields)",
        [{"role": m["role"], "content": m.get("content") or ""} for m in turn1_it3])
