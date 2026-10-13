"""Generic context-compaction test: fast-jev-compaction's technique, scored by all 3 local models.

Technique (ported from tamaratran/fast-jev-compaction - study before changing):
  - NEVER summarize or rewrite text: user + assistant text stays verbatim and in order.
    Only paired tool calls and their results are compression candidates.
  - First message + newest `--preserve` messages are pinned (their calls never touched).
  - For every non-pinned call the judge answers 2 keep-probability questions in one pass:
      call_stays   - knowing this call was made (with its input) still matters for what happens next
      result_stays - the full output is still needed verbatim; re-running would not do
  - Decisions against --threshold (Jev default 0.5):
      p_result >= t             -> keep call and result verbatim
      else p_call  >= t         -> keep call, truncate result to first N chars + one-line note
      else                      -> drop call together with its result
  - No result ever survives without its call; messages emptied by the pass are removed.

Session data: compaction_sessions.json - 10 multi-turn agent sessions; each turn is
  user input -> {thinking -> call -> result}* -> decision -> summary.

Judges reuse the model loading from approval_gate_test.py (single source of truth):
  gliner  classify_text(text=window JSON, tasks {call_stays, result_stays}: [keep, drop])
          keep prob approximated from argmax confidence (span classifier has no true probs)
  laya    state {tool, args, context=window} + 2 choice questions -> real probabilities
  kev     state {tool_name, args, context=window} + 2 choice questions on /v1/systemone

What the judge sees per call (window, NOT the full session - none of our models can
take a 25k-token state like Jev does; this is the main deliberate deviation):
  goal + message position/age + neighbouring text heads + call line + result note
  (result content hidden as "ok, N chars (omitted)" - faithful to Jev).

Usage (REVIEW BEFORE FIRST RUN - do not execute blind):
  python compaction_test.py gliner
  python compaction_test.py laya --limit 3        # first 3 sessions only
  python compaction_test.py kev --session S03
  python compaction_test.py all --threshold 0.5 --preserve 6 --truncate-head 300

Outputs:
  experiments/compaction/{model}/{sid}_before.txt     full original context
  experiments/compaction/{model}/{sid}_after.txt      context after compression
  experiments/compaction/{model}/{sid}_decisions.csv  per-call p_call/p_result/action
  experiments/compaction/results_{model}.csv          per-session stats for all models
"""
import os
os.environ.setdefault("USE_TF", "0")                   # TF 2.18 on this machine: avoids a load deadlock
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")  # Windows: no symlink privilege -> copy cache
import sys, csv, json, time, argparse, gc
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from approval_gate_test import GlinerGate, LayaGate, KevGate

SESSIONS_FILE = "compaction_sessions.json"
OUT_DIR = "experiments/compaction"
INPUT_CHARS = 1000    # Jev stage 1: tool input cap shown in the window
TEXT_HEAD = 160       # neighbour text head shown per side of the call

DECISION_COLS = ["session_id", "call_id", "tool", "turn", "pinned", "p_call", "p_result",
                 "action", "reason", "result_chars"]
SUMMARY_COLS = ["session_id", "turns", "messages_before", "messages_after", "chars_before",
                "chars_after", "reduction", "calls", "kept", "results_dropped", "calls_dropped",
                "pinned", "requests", "ms"]


# ---------------------------------------------------------------- questions

def keep_questions():
    "The two choice questions asked about every tool call, answered in one forward pass."
    return {
        "call_stays": {"type": "choice",
                       "instructions": "Should this tool call stay in the conversation history? "
                                       "Knowing that the call was made, with its input, still matters "
                                       "for what the assistant does next.",
                       "criteria": {"keep": "the call still matters for what happens next",
                                    "drop": "the call is stale and no longer needed"}},
        "result_stays": {"type": "choice",
                         "instructions": "Should the full output of this tool call stay in the history "
                                         "verbatim? The assistant still needs its contents and re-running "
                                         "the tool would not do.",
                         "criteria": {"keep": "the result content is still needed verbatim",
                                      "drop": "the result is no longer needed"}},
    }


def decide(p_call, p_result, pinned, threshold):
    "Jev's decision cascade: pinned > result >= t > call >= t > drop both."
    if pinned:
        return "keep", "pinned"
    if p_result >= threshold:
        return "keep", "kept"
    if p_call >= threshold:
        return "drop_result", "result_dropped"
    return "drop_call", "call_dropped"


# ---------------------------------------------------------------- judges

class GlinerJudge:
    "GLiNER2.5-Decide: span classification over the window JSON; binary keep prob from argmax conf."
    name = "gliner"
    model_name = "fastino/GLiNER2.5-Decide"
    note = "keep prob = conf if label==keep else 1-conf (approximation)"
    tasks = {"call_stays": ["keep", "drop"], "result_stays": ["keep", "drop"]}

    def __init__(self):
        self.gate = GlinerGate()

    def ask(self, window, call):
        text = json.dumps(window, ensure_ascii=False)
        out = self.gate.model.classify_text(text, self.tasks, include_confidence=True)
        probs = {}
        for key, res in out.items():
            conf = float(res["confidence"])
            probs[key] = conf if res["label"] == "keep" else 1.0 - conf
        return probs

    def close(self):
        del self.gate
        gc.collect()


class LayaJudge:
    "Laya typed-decisions: structured state + choice questions, real probabilities."
    name = "laya"
    model_name = "convaiinnovations/laya (typed-decisions)"
    note = "probabilities from typed decision layer"

    def __init__(self):
        self.gate = LayaGate()

    def ask(self, window, call):
        state = {"tool": call["tool"], "args": call["input"], "context": render_window(window)}
        a = self.gate.agent.predict(state, keep_questions())["answers"]
        return {"call_stays": float(a["call_stays"]["probabilities"]["keep"]),
                "result_stays": float(a["result_stays"]["probabilities"]["keep"])}

    def close(self):
        del self.gate
        gc.collect()


class KevJudge:
    "Kev-0.8B: same typed contract served over /v1/systemone (server spawned by KevGate)."
    name = "kev"
    model_name = "jaredpalmer/kev-0.8b"
    note = "probabilities from typed decision layer"

    def __init__(self):
        self.gate = KevGate()

    def ask(self, window, call):
        import urllib.request
        state = {"tool_name": call["tool"], "args": call["input"], "context": render_window(window)}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.gate.port}/v1/systemone",
            data=json.dumps({"state": state, "model": "kev-latest",
                             "questions": keep_questions()}).encode(),
            headers={"Content-Type": "application/json"})
        for attempt in range(3):
            try:
                a = json.loads(urllib.request.urlopen(req, timeout=180).read())["answers"]
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"    retry after {type(e).__name__}: {e}")
                time.sleep(3)
        return {"call_stays": float(a["call_stays"]["probabilities"]["keep"]),
                "result_stays": float(a["result_stays"]["probabilities"]["keep"])}

    def close(self):
        self.gate._stop()
        gc.collect()


JUDGES = {"gliner": GlinerJudge, "laya": LayaJudge, "kev": KevJudge}


# ---------------------------------------------------------------- sessions -> messages

def load_sessions(limit=None, only=None):
    with open(SESSIONS_FILE, encoding="utf-8") as f:
        sessions = json.load(f)["sessions"]
    if only:
        sessions = [s for s in sessions if s["id"].lower() == only.lower()]
    if limit:
        sessions = sessions[:limit]
    return sessions


def session_messages(session):
    "Turn schema -> flat transcript messages (Claude-Code-like): user, thinking+calls, results, decision+summary."
    messages, turn_of = [], {}
    for t in session["turns"]:
        messages.append({"role": "user", "text": t.get("user", ""), "toolUses": []})
        steps = t.get("steps", [])
        for si, step in enumerate(steps):
            call, res = step["call"], step["result"]
            turn_of[call["id"]] = t["turn"]
            messages.append({"role": "assistant",
                             "text": t.get("thinking", "") if si == 0 else "",
                             "toolUses": [{"tool_use_id": call["id"], "tool": call["tool"],
                                           "input": call.get("input", {})}]})
            messages.append({"role": "user", "text": "", "toolUses": [],
                             "toolResults": [{"tool_use_id": call["id"], "text": res.get("text", ""),
                                              "isError": bool(res.get("isError"))}]})
        tail = "\n\n".join(x for x in (t.get("decision", ""), t.get("summary", "")) if x)
        messages.append({"role": "assistant", "text": tail, "toolUses": []})
    return messages, turn_of


def goal_of(session):
    "Default goal: session task + last user input (Jev uses the last 3 prompts)."
    parts = []
    if session.get("task", "").strip():
        parts.append(session["task"].strip())
    users = [t.get("user", "") for t in session["turns"]]
    if users and users[-1].strip():
        parts.append("latest user input: " + (users[-1][:300] + ("..." if len(users[-1]) > 300 else "")))
    return "\n".join(parts)


def is_pinned(index, total, preserve):
    return index == 0 or index >= total - preserve


def collect_calls(messages, preserve, turn_of):
    "Pair every tool_use with its tool_result by id; calls without results are never candidates."
    results = {}
    for i, m in enumerate(messages):
        for r in m.get("toolResults", []):
            results[r["tool_use_id"]] = (i, r)
    n = len(messages)
    calls = []
    for i, m in enumerate(messages):
        for t in m.get("toolUses", []):
            if t["tool_use_id"] not in results:
                continue
            ri, r = results[t["tool_use_id"]]
            calls.append({
                "id": t["tool_use_id"], "tool": t["tool"], "input": t.get("input", {}),
                "call_index": i, "result_index": ri, "result_chars": len(r["text"]),
                "is_error": bool(r.get("isError")),
                "pinned": is_pinned(i, n, preserve) or is_pinned(ri, n, preserve),
                "turn": turn_of.get(t["tool_use_id"], 0),
            })
    return calls


# ---------------------------------------------------------------- window (what the judge sees)

def truncate(text, limit):
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "..."


def neighbour_text(messages, index, direction, limit):
    j = index + direction
    while 0 <= j < len(messages):
        t = messages[j].get("text", "").strip()
        if t:
            return t[:limit] + ("..." if len(t) > limit else "")
        j += direction
    return ""


def build_window(call, messages, goal):
    return {
        "goal": goal,
        "position": f"message {call['call_index']} of {len(messages)} (turn {call['turn']}), "
                    f"{len(messages) - 1 - max(call['call_index'], call['result_index'])} messages after it",
        "before_text": neighbour_text(messages, call["call_index"], -1, TEXT_HEAD),
        "call": f"{call['id']} {call['tool']} "
                + truncate(json.dumps(call.get("input", {}), ensure_ascii=False), INPUT_CHARS),
        "result_note": f"{'error' if call['is_error'] else 'ok'}, {call['result_chars']} chars (omitted)",
        "after_text": neighbour_text(messages, call["result_index"], +1, TEXT_HEAD),
    }


def render_window(window):
    parts = [f"goal: {window['goal']}", window["position"]]
    if window["before_text"]:
        parts.append("earlier: " + window["before_text"])
    parts.append("call: " + window["call"])
    parts.append("result: " + window["result_note"])
    if window["after_text"]:
        parts.append("next: " + window["after_text"])
    return "\n".join(parts)


# ---------------------------------------------------------------- rebuild (Jev's applyDecisions)

def truncate_result_text(text, is_error, head_chars):
    if len(text) <= head_chars + 120:
        return text
    head = text[:head_chars] + "\n" if head_chars > 0 else ""
    return (f"{head}[compaction truncated {len(text) - head_chars} chars of this tool result"
            f"{' (error)' if is_error else ''}; re-run the tool if needed]")


def apply_decisions(messages, actions, head_chars):
    "actions: tool_use_id -> 'drop_call' | 'drop_result'. No result without its call; emptied messages removed."
    kept = []
    for m in messages:
        uses = m.get("toolUses", [])
        results = m.get("toolResults", [])
        touched = (any(t["tool_use_id"] in actions for t in uses)
                   or any(r["tool_use_id"] in actions for r in results))
        if not touched:
            kept.append(m)
            continue
        new_uses = [t for t in uses if actions.get(t["tool_use_id"]) != "drop_call"]
        new_results = []
        for r in results:
            act = actions.get(r["tool_use_id"])
            if act == "drop_call":
                continue
            if act == "drop_result":
                new_results.append({**r, "text": truncate_result_text(
                    r["text"], bool(r.get("isError")), head_chars)})
            else:
                new_results.append(r)
        unchanged = (len(new_uses) == len(uses) and all(a is b for a, b in zip(new_uses, uses))
                     and len(new_results) == len(results)
                     and all(a.get("text") == b.get("text") for a, b in zip(new_results, results)))
        if unchanged:
            kept.append(m)
            continue
        text = m.get("text", "")
        if not text.strip() and not new_uses and not new_results:
            continue
        rebuilt = {"role": m["role"], "text": text, "toolUses": new_uses}
        if new_results:
            rebuilt["toolResults"] = new_results
        kept.append(rebuilt)
    return kept


def message_chars(m):
    total = len(m.get("text", ""))
    for t in m.get("toolUses", []):
        total += len(json.dumps(t.get("input", {}), ensure_ascii=False))
    for r in m.get("toolResults", []):
        total += len(r.get("text", ""))
    return total


def render(messages):
    "Human-readable transcript for before/after files."
    lines = []
    for m in messages:
        lines.append(f"=== {m['role']} ===")
        if m.get("text"):
            lines.append(m["text"])
        for t in m.get("toolUses", []):
            lines.append(f"  -> TOOL {t['tool']} {json.dumps(t.get('input', {}), ensure_ascii=False)}")
        for r in m.get("toolResults", []):
            tag = "error" if r.get("isError") else "ok"
            lines.append(f"  <- RESULT [{tag}, {len(r.get('text', ''))} chars]")
            for ln in r.get("text", "").splitlines():
                lines.append(f"     {ln}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- one session through one model

def compact_session(judge, session, threshold, preserve, head_chars):
    sid = session["id"]
    messages, turn_of = session_messages(session)
    goal = goal_of(session)
    calls = collect_calls(messages, preserve, turn_of)
    chars_before = sum(message_chars(m) for m in messages)

    t0 = time.perf_counter()
    decisions, requests = [], 0
    for call in calls:
        if call["pinned"]:
            p_call = p_result = 1.0
        else:
            probs = judge.ask(build_window(call, messages, goal), call)
            p_call, p_result = probs["call_stays"], probs["result_stays"]
            requests += 1
        action, reason = decide(p_call, p_result, call["pinned"], threshold)
        decisions.append({"session_id": sid, "call_id": call["id"], "tool": call["tool"],
                          "turn": call["turn"], "pinned": "Y" if call["pinned"] else "N",
                          "p_call": round(p_call, 4), "p_result": round(p_result, 4),
                          "action": action, "reason": reason, "result_chars": call["result_chars"]})

    actions = {d["call_id"]: d["action"] for d in decisions if d["action"] != "keep"}
    after = apply_decisions(messages, actions, head_chars)
    chars_after = sum(message_chars(m) for m in after)
    ms = int((time.perf_counter() - t0) * 1000)

    out_dir = os.path.join(OUT_DIR, judge.name)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{sid}_before.txt"), "w", encoding="utf-8") as f:
        f.write(render(messages))
    with open(os.path.join(out_dir, f"{sid}_after.txt"), "w", encoding="utf-8") as f:
        f.write(render(after))
    with open(os.path.join(out_dir, f"{sid}_decisions.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DECISION_COLS)
        w.writeheader()
        w.writerows(decisions)

    counts = {k: sum(1 for d in decisions if d["reason"] == k)
              for k in ("kept", "result_dropped", "call_dropped", "pinned")}
    return {"session_id": sid, "turns": len(session["turns"]),
            "messages_before": len(messages), "messages_after": len(after),
            "chars_before": chars_before, "chars_after": chars_after,
            "reduction": round((chars_before - chars_after) / chars_before, 4) if chars_before else 0.0,
            "calls": len(calls), "kept": counts["kept"],
            "results_dropped": counts["result_dropped"], "calls_dropped": counts["call_dropped"],
            "pinned": counts["pinned"], "requests": requests, "ms": ms}


# ---------------------------------------------------------------- driver

def run_model(name, sessions, args):
    t = time.perf_counter()
    judge = JUDGES[name]()
    print(f"  {judge.model_name} ready in {time.perf_counter() - t:.0f}s"
          + (f"  ({judge.note})" if getattr(judge, "note", "") else ""))
    rows = []
    try:
        for session in sessions:
            row = compact_session(judge, session, args.threshold, args.preserve, args.truncate_head)
            rows.append(row)
            print(f"    {row['session_id']}: msgs {row['messages_before']}->{row['messages_after']}, "
                  f"chars {row['chars_before']}->{row['chars_after']} (-{row['reduction']:.0%})  "
                  f"[keep {row['kept']}, trunc {row['results_dropped']}, "
                  f"drop {row['calls_dropped']}, pinned {row['pinned']}]  {row['ms']}ms")
    finally:
        judge.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"results_{name}.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLS)
        w.writeheader()
        w.writerows(rows)

    cb = sum(r["chars_before"] for r in rows)
    ca = sum(r["chars_after"] for r in rows)
    print(f"  {judge.model_name}: {len(rows)} sessions, chars {cb} -> {ca} "
          f"(-{(cb - ca) / cb:.0%} overall) -> {out_path}")
    return rows


def main():
    ap = argparse.ArgumentParser(description="Context compaction over the 10 example sessions")
    ap.add_argument("model", choices=[*JUDGES, "all"])
    ap.add_argument("--limit", type=int, default=None, help="only first N sessions (smoke)")
    ap.add_argument("--session", default=None, help="only this session id, e.g. S03")
    ap.add_argument("--threshold", type=float, default=0.5, help="keep probability threshold (Jev default 0.5)")
    ap.add_argument("--preserve", type=int, default=6, help="newest messages never touched")
    ap.add_argument("--truncate-head", type=int, default=300, help="chars of a dropped result kept")
    args = ap.parse_args()

    sessions = load_sessions(limit=args.limit, only=args.session)
    if not sessions:
        print("  no sessions matched")
        return
    names = list(JUDGES) if args.model == "all" else [args.model]
    print(f"  {len(sessions)} session(s), models: {', '.join(names)}")
    for name in names:
        run_model(name, sessions, args)
    print("\n  done - compare experiments/compaction/{model}/*_before.txt vs *_after.txt "
          "and decisions.csv for what each model chose to keep")


if __name__ == "__main__":
    main()
