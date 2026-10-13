"""
experiments/plan00_smoke_check.py
Junior-engineer style smoke test for the Plan 00 deliverables.

Runs WITHOUT any real LLM (deterministic FakeModel) and WITHOUT touching
production code. It verifies:

  A. replay_context_bench
     A1. tool JSON envelope stripping (Engine 3)
     A2. reused_cached_result=True collapses to a [CACHED_RESULT] stub
     A3. masking spills real files to disk and inserts [OUTPUT_MASKED]
     A4. summarization fires at 80% of a SMALL window (no 100k floor)
     A5. real repo traces still replay (default trace loads)
  B. prototype_agent (simple agent loop)
     B1. tools work standalone (create/read/edit round-trip)
     B2. tool loop executes calls and stores the wire format
     B3. final (no tool_calls) assistant reply is returned AND printed
     B4. provider/model resolution handles --provider + bare model names
     B5. entrypoint is importable and exposes main()
     B6. ModelProfile strategy is wired into the prototype (registry + explicit)
     B7. profile strategy is traceable (profile_resolved, session_start profile,
         context_limit/context_pct on llm_request)

Usage:
    python experiments/plan00_smoke_check.py
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import contextlib
from typing import Any, Dict, List

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prototype_agent as pa            # noqa: E402
import replay_context_bench as bench    # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# --------------------------------------------------------------------------- #
# Fake model: deterministic scripted responses
# --------------------------------------------------------------------------- #
class FakeModel:
    """Stands in for ModelClient. Returns canned tool_calls / text."""

    def __init__(self, script: List[Dict[str, Any]]):
        self.script = script
        self.calls: List[List[Dict[str, Any]]] = []

    def chat(self, messages, tools=None, max_tokens=2048):
        self.calls.append(list(messages))
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        step = self.script[idx]
        return {
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


def fn_call(name: str, args: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# --------------------------------------------------------------------------- #
# A. Bench checks
# --------------------------------------------------------------------------- #
def test_bench() -> None:
    print("\n[A] replay_context_bench")

    # A1 + A2: serialization / cached-result collapsing
    stats = {"distilled": 0, "distill_tokens_saved": 0, "cached_hits": 0}
    fat_tool = json.dumps({"tool": "web_search", "success": True,
                           "reused_cached_result": False, "content": "x" * 4000})
    cached_tool = json.dumps({"tool": "url_fetch", "success": True,
                              "reused_cached_result": True, "content": "y" * 4000})
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "tool", "name": "web_search", "content": fat_tool},
        {"role": "tool", "name": "url_fetch", "content": cached_tool},
    ]
    out = bench.distill_tool_messages(msgs, stats)
    check("A1 tool JSON envelope stripped",
          out[2]["content"] == "x" * 4000, out[2]["content"][:60])
    check("A2 reused_cached_result=True -> stub",
          out[3]["content"].startswith(bench.CACHED_INDICATOR) and stats["cached_hits"] == 1,
          out[3]["content"][:60])

    # A3: masking spills to disk (tool message must sit outside the protection window)
    big = [{"role": "system", "content": "sys"},
           {"role": "tool", "name": "bash", "content": "z" * 9000},
           {"role": "assistant", "content": "ok"},
           {"role": "user", "content": "next question"}]
    mstats = {"masked": 0, "mask_tokens_saved": 0, "spill_files": 0, "spill_paths": []}
    masked = bench.mask_tool_outputs(big, "smoke-session", mask_threshold=100, stats=mstats)
    spilled = mstats["spill_paths"][0] if mstats["spill_paths"] else ""
    check("A3a masking inserted [OUTPUT_MASKED]",
          bench.MASKING_INDICATOR in masked[1]["content"], masked[1]["content"][:40])
    check("A3b spill file exists on disk", bool(spilled) and os.path.exists(spilled), spilled)
    if spilled and os.path.exists(spilled):
        with open(spilled, "r", encoding="utf-8") as fh:
            body = fh.read()
        check("A3c spill file holds the original payload", body == "z" * 9000, str(len(body)))

    # A3d: a payload that QUOTES the marker is still maskable
    quoting = [{"role": "system", "content": "sys"},
               {"role": "tool", "name": "read_file",
                "content": 'code says MASKING_INDICATOR = "[OUTPUT_MASKED]" plus padding ' + "p" * 9000},
               {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "next"}]
    mstats2 = {"masked": 0, "mask_tokens_saved": 0, "spill_files": 0, "spill_paths": []}
    masked2 = bench.mask_tool_outputs(quoting, "smoke-session-2", mask_threshold=100, stats=mstats2)
    check("A3d payload quoting the marker still gets masked",
          masked2[1]["content"].startswith(bench.MASKING_INDICATOR), masked2[1]["content"][:40])

    # A4: summarization on a small window (no 100k floor)
    cstats = {"compressions": 0, "compress_tokens_saved": 0}
    history = [{"role": "system", "content": "sys"}]
    for i in range(30):
        history.append({"role": "user", "content": f"question {i} " + "w" * 400})
        history.append({"role": "assistant", "content": f"answer {i} " + "v" * 400})
    compressed = bench.compress_old_turns(history, compress_threshold=2000, stats=cstats)
    check("A4a summarization fired without 100k floor", cstats["compressions"] >= 1,
          f"passes={cstats['compressions']}")
    check("A4b compressed context shrank",
          bench.messages_tokens(compressed) < bench.messages_tokens(history),
          f"{bench.messages_tokens(history)} -> {bench.messages_tokens(compressed)}")
    check("A4c digest message present",
          any(str(m.get("content", "")).startswith(bench.SUMMARY_INDICATOR) for m in compressed))

    # A5: real trace replays
    default_trace = os.path.join(REPO_ROOT, bench.DEFAULT_TRACE)
    check("A5a default acceptance trace exists", os.path.exists(default_trace), default_trace)
    if os.path.exists(default_trace):
        result = bench.benchmark_trace(default_trace, 32768, verbose=False)
        check("A5b real trace replayed with rows", result["requests"] > 0 and len(result["rows"]) > 0,
              str(result))
        check("A5c real trace wrote spill files for bloated turns",
              result["totals"].get("spill_files", 0) > 0, str(result["totals"]))


# --------------------------------------------------------------------------- #
# B. Prototype checks
# --------------------------------------------------------------------------- #
def test_prototype() -> None:
    print("\n[B] prototype_agent")

    tmp = tempfile.gettempdir()
    target = os.path.join(tmp, "smoke_p00.txt")

    # B1: tools work standalone
    created = pa.tool_create_file({"file_path": target, "content": "hello-plan00"})
    check("B1a create_file works", os.path.exists(target), created)
    read_back = pa.tool_read_file({"file_path": target})
    check("B1b read_file round-trips", "hello-plan00" in read_back, read_back[:60])
    edited = pa.tool_edit_file({"file_path": target, "old_str": "hello-plan00",
                                "new_str": "edited-plan00"})
    read_back = pa.tool_read_file({"file_path": target})
    check("B1c edit_file round-trips", "edited-plan00" in read_back and "ERROR" not in edited)

    # B2 + B3: tool loop executes, wire format stored, final reply printed
    script = [
        {"tool_calls": [fn_call("create_file",
                                {"file_path": os.path.join(tmp, "loop_p00.txt"),
                                 "content": "from loop"}, "c1")]},
        {"content": "I created the file and it is ready."},
    ]
    fake = FakeModel(script)
    session: List[Dict[str, Any]] = [{"role": "system", "content": pa.SYSTEM_PROMPT}]

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        final = pa.run_turn(fake, session, "make a file from the fake model")
    printed = buffer.getvalue()

    roles = [m.get("role") for m in session]
    check("B2a session grows system/user/assistant/tool/assistant",
          roles == ["system", "user", "assistant", "tool", "assistant"], str(roles))
    check("B2b tool result stored in the wire envelope",
          session[3]["content"].startswith("{\"tool\": \"create_file\"")
          and "\"reused_cached_result\"" in session[3]["content"],
          session[3]["content"][:80])
    check("B2c tool was actually executed",
          os.path.exists(os.path.join(tmp, "loop_p00.txt")))
    check("B3a final reply returned to caller",
          final == "I created the file and it is ready.", final)
    check("B3b final reply printed for the user",
          "[agent] I created the file and it is ready." in printed, printed[:120])

    # B4: provider/model resolution
    check("B4a --provider + bare model",
          pa.resolve_provider_model("gemma4:cloud", "ollama") == ("ollama", "gemma4:cloud"),
          str(pa.resolve_provider_model("gemma4:cloud", "ollama")))
    check("B4b bare :cloud model defaults to ollama",
          pa.resolve_provider_model("gemma4:cloud", None) == ("ollama", "gemma4:cloud"))
    check("B4c prefixed model wins",
          pa.resolve_provider_model("gemini/gemini-2.5-flash", None) == ("gemini", "gemini-2.5-flash"))

    # B5: entrypoint plumbing
    check("B5 entrypoint is importable and exposes main()", callable(getattr(pa, "main", None)))

    # B6: ModelProfile strategy is wired into the prototype
    prof = pa.ModelProfile.resolve("gemma4:cloud")
    check("B6a prototype resolves registry profile",
          prof.source == "registry" and prof.tier == "standard"
          and prof.context_length == 128000, str(prof.to_dict()))
    prof_cfg = pa.ModelProfile.resolve(
        "gemma4:cloud", {"model_tier": "small", "context_length": 16000})
    check("B6b explicit --model-tier/--context-length win",
          prof_cfg.source == "config" and prof_cfg.tier == "small"
          and prof_cfg.context_length == 16000, str(prof_cfg.to_dict()))

    # B7: profile strategy is traceable
    trace_path = os.path.join(tmp, "smoke_profile_trace.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)
    tr = pa.PrototypeTracer(path=trace_path, enabled=True, provider="ollama",
                            model="gemma4:cloud", profile=prof.to_dict())
    tr.profile_resolved()
    tr.session_start("sys")
    tr.llm_request([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
                   pa.TOOL_SCHEMAS)
    tr.close()
    with open(trace_path, "r", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    kinds = [r["type"] for r in records]
    check("B7a profile_resolved event written first",
          bool(kinds) and kinds[0] == "profile_resolved"
          and records[0]["profile"]["source"] == "registry", str(kinds))
    sess = [r for r in records if r["type"] == "session_start"]
    check("B7b session_start embeds profile",
          bool(sess) and sess[0]["profile"]["tier"] == "standard", str(sess))
    llm_recs = [r for r in records if r["type"] == "llm_request"]
    check("B7c llm_request carries context_limit/pct",
          bool(llm_recs) and llm_recs[0].get("context_limit") == 128000
          and isinstance(llm_recs[0].get("context_pct"), float), str(llm_recs[:1]))


def main() -> int:
    os.chdir(REPO_ROOT)
    test_bench()
    test_prototype()
    print(f"\n===== smoke check: {PASS} passed, {FAIL} failed =====")
    if FAIL:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
