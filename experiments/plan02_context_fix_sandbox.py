"""
Plan 02 sandbox — Engine 1 masking & compaction fixes, BEFORE vs AFTER.

Runs each of the three root-bug scenarios twice:
  BEFORE = real framework classes (current, buggy baseline)
  AFTER  = experiments.context_engine prototype (the proposed fixes)

Prints a side-by-side verdict and writes a JSONL trace per scenario under
debug/traces/plan02/ for deeper analysis. Nothing here touches the framework.

Usage:
    python experiments/plan02_context_fix_sandbox.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

# Windows console defaults to cp1252, which can't encode the '→' the framework
# prints during compression. Force utf-8 so compression doesn't crash mid-run.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.model_profile import ModelProfile
from logicore.runtime.context.manager import ContextWindowManager as BuggyManager
from logicore.runtime.context.compression import CompressionStatus
from logicore.runtime.context.masking import MASKING_INDICATOR

from experiments.context_engine import FixedContextWindowManager

TRACE_DIR = os.path.join("debug", "traces", "plan02")
os.makedirs(TRACE_DIR, exist_ok=True)


class FakeSession:
    def __init__(self, session_id: str, system_message: str = "system"):
        self.session_id = session_id
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]


class FakeLLM:
    def __init__(self, summary: str = "SUMMARY: earlier turns compressed; key facts retained."):
        self.summary = summary
        self.calls: List[str] = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages[0].get("content", "") if messages else "")

        class _R:
            content = self.summary

        return _R()


def _config(window: int, tier: str) -> RuntimeConfig:
    config = RuntimeConfig.from_settings()
    profile = ModelProfile.resolve("sandbox-model", {"context_length": window, "model_tier": tier})
    config.apply_model_profile(profile)
    return config


def make_buggy(window: int, tier: str, session: FakeSession, llm=None) -> BuggyManager:
    return BuggyManager(
        config=_config(window, tier),
        llm_provider=llm,
        model_name="sandbox-model",
    )


def make_fixed(window: int, tier: str, session: FakeSession, llm=None) -> FixedContextWindowManager:
    return FixedContextWindowManager(
        config=_config(window, tier),
        llm_provider=llm,
        model_name="sandbox-model",
        session_provider=lambda sid: session if sid == session.session_id else None,
    )


_PARA = (
    "The quick brown fox jumps over the lazy dog while parsing config files "
    "and running tools against the local sandbox environment for testing. "
)


def tool_msg(name: str, tokens: int) -> Dict[str, Any]:
    # cl100k on repetitive prose lands near ~5.8 chars/token; overshoot a bit so
    # the estimator reliably clears the protection window for the target size.
    repeats = max(1, int(tokens * 6.0 / len(_PARA)))
    return {"role": "tool", "name": name, "content": _PARA * repeats}


def user_msg(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": text}


def assistant_msg(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


def log(trace: List[Dict[str, Any]], event: str, **data: Any) -> None:
    trace.append({"ts": datetime.now().isoformat(), "event": event, **data})


def write_trace(name: str, trace: List[Dict[str, Any]]) -> str:
    path = os.path.join(TRACE_DIR, f"{name}_{int(time.time())}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for row in trace:
            f.write(json.dumps(row) + "\n")
    return path


def _placeholder(session: FakeSession) -> bool:
    return any(MASKING_INDICATOR in (m.get("content") or "") for m in session.messages)


def _summary_in_session(session: FakeSession) -> bool:
    return any("Previous Conversation Summary" in (m.get("content") or "") for m in session.messages)


# ---------------------------------------------------------------------------
# Scenario 1: masking before the compression gate (Bug 1)
# ---------------------------------------------------------------------------
def scenario_masking_before_compression() -> Tuple[bool, bool]:
    name = "01_masking_before_compression"
    # 64k window gives room: tool-heavy enough to mask (~30k > ~23k protection)
    # but total stays under the ~52k compression threshold, so masking must fire
    # WITHOUT summarization. On a 32k window these two thresholds collide.
    window, tier = 65536, "standard"

    def _build(kind: str):
        session = FakeSession("s1")
        session.messages.append(tool_msg("read_file", 40000))
        session.messages.append(user_msg("what did the file say?"))
        session.messages.append(assistant_msg("It contained config values."))
        mgr = (make_fixed if kind == "after" else make_buggy)(window, tier, session, llm=FakeLLM())
        return session, mgr

    def _run(kind: str) -> Tuple[bool, List[Dict[str, Any]]]:
        session, mgr = _build(kind)
        trace: List[Dict[str, Any]] = []
        log(trace, "setup", mode=kind, window=window, tier=tier,
            compression_threshold=mgr.budget.compression_threshold,
            original_tokens=mgr._estimate_messages_tokens(session.messages))
        result, _ = asyncio.run(mgr.manage(session.messages, "s1"))
        persisted = _placeholder(session)
        log(trace, "result", masked=result.masked, compressed=result.compressed,
            original_tokens=result.original_tokens, final_tokens=result.final_tokens,
            session_persisted=persisted)
        write_trace(f"{name}_{kind}", trace)
        # PASS = masking ran even though total < compression threshold.
        ok = result.masked and persisted and not result.compressed
        print(f"  [{kind:6s}] masked={result.masked} compressed={result.compressed} "
              f"persisted={persisted} tokens={result.original_tokens}->{result.final_tokens} "
              f"{'PASS' if ok else 'FAIL'}")
        return ok, trace

    print(f"\n[{name}] tool-heavy but under compression threshold")
    before_ok, _ = _run("before")
    after_ok, _ = _run("after")
    path = os.path.join(TRACE_DIR, f"{name}_*.jsonl")
    print(f"  traces: {path}")
    return before_ok, after_ok


# ---------------------------------------------------------------------------
# Scenario 2: compression on a small window (Bug 2 — no 100k floor)
# ---------------------------------------------------------------------------
def scenario_compression_small_window() -> Tuple[bool, bool]:
    name = "02_compression_small_window"
    window, tier = 32768, "small"

    def _build(kind: str):
        session = FakeSession("s2")
        filler = ("y" * 4) * 2000  # ~2k tokens each
        for i in range(12):
            session.messages.append(user_msg(f"turn {i}: " + filler))
            session.messages.append(assistant_msg(f"reply {i}: " + filler))
        llm = FakeLLM()
        mgr = (make_fixed if kind == "after" else make_buggy)(window, tier, session, llm=llm)
        return session, mgr, llm

    def _run(kind: str) -> Tuple[bool, List[Dict[str, Any]]]:
        session, mgr, llm = _build(kind)
        trace: List[Dict[str, Any]] = []
        original_count = len(session.messages)

        # Direct compress() to prove the 100k floor is gone on a small window.
        comp = asyncio.run(mgr.compression_service.compress(list(session.messages), "s2"))
        floor_gone = comp.status != CompressionStatus.SKIPPED_SMALL

        # Full pipeline: compress then (for the prototype) persist back.
        result, _ = asyncio.run(mgr.manage(list(session.messages), "s2"))
        compacted = len(session.messages) < original_count

        log(trace, "mode", mode=kind, window=window, tier=tier,
            compression_threshold=mgr.budget.compression_threshold)
        log(trace, "compress_direct", status=comp.status.value,
            original_tokens=comp.original_tokens, compressed_tokens=comp.compressed_tokens,
            floor_gone=floor_gone, llm_calls=len(llm.calls))
        log(trace, "result", compressed=result.compressed, truncated=result.truncated,
            final_tokens=result.final_tokens, original_count=original_count,
            session_count=len(session.messages), session_compacted=compacted)
        write_trace(f"{name}_{kind}", trace)
        # PASS = floor gone AND compression ran AND the compacted result was
        # persisted back to the session (the prototype's Bug 3 fix).
        ok = floor_gone and result.compressed and compacted
        print(f"  [{kind:6s}] floor_gone={floor_gone} compressed={result.compressed} "
              f"session {original_count}->{len(session.messages)} msgs "
              f"compacted={compacted} {'PASS' if ok else 'FAIL'}")
        return ok, trace

    print(f"\n[{name}] summarization on a 32k window (old floor was 100k)")
    before_ok, _ = _run("before")
    after_ok, _ = _run("after")
    print(f"  traces: {os.path.join(TRACE_DIR, name + '_*.jsonl')}")
    return before_ok, after_ok


# ---------------------------------------------------------------------------
# Scenario 3: masking persists across turns (Bug 3 — read-only path)
# ---------------------------------------------------------------------------
def scenario_masking_persists_across_turns() -> Tuple[bool, bool]:
    name = "03_masking_persists_across_turns"
    window, tier = 65536, "standard"

    def _build(kind: str):
        session = FakeSession("s3")
        session.messages.append(tool_msg("bash", 40000))
        session.messages.append(user_msg("run it"))
        session.messages.append(assistant_msg("done"))
        mgr = (make_fixed if kind == "after" else make_buggy)(window, tier, session, llm=FakeLLM())
        return session, mgr

    def _run(kind: str) -> Tuple[bool, List[Dict[str, Any]]]:
        session, mgr = _build(kind)
        trace: List[Dict[str, Any]] = []
        asyncio.run(mgr.manage(session.messages, "s3"))
        turn1 = _placeholder(session)
        result2, _ = asyncio.run(mgr.manage(session.messages, "s3"))
        turn2 = _placeholder(session)
        log(trace, "mode", mode=kind, window=window, tier=tier)
        log(trace, "turn1", session_persisted=turn1, session_msg_count=len(session.messages))
        log(trace, "turn2", still_masked=turn2, final_tokens=result2.final_tokens)
        write_trace(f"{name}_{kind}", trace)
        ok = turn1 and turn2
        print(f"  [{kind:6s}] turn1_persisted={turn1} turn2_still_masked={turn2} "
              f"{'PASS' if ok else 'FAIL'}")
        return ok, trace

    print(f"\n[{name}] masked history must survive into the next turn")
    before_ok, _ = _run("before")
    after_ok, _ = _run("after")
    print(f"  traces: {os.path.join(TRACE_DIR, name + '_*.jsonl')}")
    return before_ok, after_ok


def main() -> int:
    print("=" * 68)
    print("  Plan 02 sandbox — Engine 1 masking & compaction (before vs after)")
    print("  BEFORE = real framework (buggy)   AFTER = experiments prototype (fixed)")
    print("=" * 68)

    scenarios = [
        ("masking before compression (Bug 1)", scenario_masking_before_compression),
        ("compression on small window (Bug 2)", scenario_compression_small_window),
        ("masking persists across turns (Bug 3)", scenario_masking_persists_across_turns),
    ]

    results = []
    for label, fn in scenarios:
        before_ok, after_ok = fn()
        results.append((label, before_ok, after_ok))

    print("\n" + "=" * 68)
    print("  SUMMARY")
    print("=" * 68)
    print(f"  {'scenario':40s} {'BEFORE':>8s} {'AFTER':>8s}")
    print(f"  {'-'*40} {'-'*8} {'-'*8}")
    all_after = True
    for label, before_ok, after_ok in results:
        print(f"  {label:40s} {'PASS' if before_ok else 'FAIL':>8s} {'PASS' if after_ok else 'FAIL':>8s}")
        all_after = all_after and after_ok
    print("-" * 68)
    print(f"  prototype (AFTER): {'ALL PASS' if all_after else 'SOME FAILED'}")
    print(f"  traces: {TRACE_DIR}/")
    return 0 if all_after else 1


if __name__ == "__main__":
    raise SystemExit(main())

