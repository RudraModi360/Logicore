"""
Plan 02 sandbox: Engine 1 session-context fixes (masking & compaction).

Deterministic replay of the three root bugs against the REAL framework classes
(ContextWindowManager / CompressionService / ToolOutputMaskingService /
TokenBudget), wired the same way the Agent wires them (with ModelProfile applied
and a live session_provider). Emits a JSONL trace per scenario under
debug/traces/plan02/ for the PO to analyze.

Usage:
    python experiments/plan02_context_fix_check.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

# Make repo root importable when run as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.model_profile import ModelProfile
from logicore.runtime.context.manager import ContextWindowManager
from logicore.runtime.context.compression import CompressionService, CompressionStatus
from logicore.runtime.context.token_budget import TokenBudget, TokenCategory
from logicore.runtime.context.masking import MASKING_INDICATOR

TRACE_DIR = os.path.join("debug", "traces", "plan02")
os.makedirs(TRACE_DIR, exist_ok=True)


class FakeSession:
    """Minimal stand-in for AgentSession (exposes .messages)."""

    def __init__(self, session_id: str, system_message: str = "system"):
        self.session_id = session_id
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]


class FakeLLM:
    """Deterministic LLM that returns a fixed short summary for compression."""

    def __init__(self, summary: str = "SUMMARY: user asked about topic; assistant provided facts; pending task X."):
        self.summary = summary
        self.calls: List[str] = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages[0].get("content", "") if messages else "")
        class _R:
            content = self.summary
        return _R()


def make_manager(window: int, tier: str, session: FakeSession, llm=None) -> ContextWindowManager:
    """Build a ContextWindowManager exactly the way Agent does (profile applied)."""
    config = RuntimeConfig.from_settings()
    profile = ModelProfile.resolve(
        "sandbox-model",
        {"context_length": window, "model_tier": tier},
    )
    config.apply_model_profile(profile)
    manager = ContextWindowManager(
        config=config,
        llm_provider=llm,
        model_name="sandbox-model",
        session_provider=lambda sid: session if sid == session.session_id else None,
    )
    return manager


_PARA = (
    "The quick brown fox jumps over the lazy dog while parsing config files "
    "and running tools against the local sandbox environment for testing. "
)


def tool_msg(name: str, tokens: int) -> Dict[str, Any]:
    """A tool result message approximating `tokens` tokens (~5.3 chars/token)."""
    repeats = max(1, int(tokens * 5.3 / len(_PARA)))
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


# ---------------------------------------------------------------------------
# Scenario 1: Masking triggers BEFORE the compression gate (Bug 1)
# ---------------------------------------------------------------------------
def scenario_masking_before_compression() -> bool:
    name = "01_masking_before_compression"
    trace: List[Dict[str, Any]] = []
    window, tier = 32768, "small"
    session = FakeSession("s1")
    # ~15k tokens of tool output, but total stays well under the ~24.5k
    # compression threshold for a 32k/small window.
    session.messages.append(tool_msg("read_file", 15000))
    session.messages.append(user_msg("what did the file say?"))
    session.messages.append(assistant_msg("It contained config values."))

    manager = make_manager(window, tier, session, llm=FakeLLM())
    profile = manager.budget
    log(trace, "setup", window=window, tier=tier,
        compression_threshold=profile.compression_threshold,
        protection_threshold=manager.config.context.protection_threshold_tokens,
        original_msg_count=len(session.messages))

    result = asyncio.run(manager.manage(session.messages, "s1"))

    masked = result[0].masked
    has_placeholder = any(
        MASKING_INDICATOR in (m.get("content") or "") for m in session.messages
    )
    log(trace, "result",
        masked=masked,
        compressed=result[0].compressed,
        original_tokens=result[0].original_tokens,
        final_tokens=result[0].final_tokens,
        tokens_saved=result[0].tokens_saved,
        session_persisted=has_placeholder,
        session_msg_count=len(session.messages))
    path = write_trace(name, trace)
    ok = masked and has_placeholder
    print(f"[{name}] {'PASS' if ok else 'FAIL'} masked={masked} persisted={has_placeholder} trace={path}")
    return ok


# ---------------------------------------------------------------------------
# Scenario 2: Compression runs on a small window (Bug 2 — no 100k floor)
# ---------------------------------------------------------------------------
def scenario_compression_small_window() -> bool:
    name = "02_compression_small_window"
    trace: List[Dict[str, Any]] = []
    window, tier = 32768, "small"
    session = FakeSession("s2")
    # ~25k tokens of ordinary chat, above the ~24.5k compression threshold.
    filler = ("y" * 4) * 2000  # ~2k tokens each
    for i in range(12):
        session.messages.append(user_msg(f"turn {i}: " + filler))
        session.messages.append(assistant_msg(f"reply {i}: " + filler))

    llm = FakeLLM()
    manager = make_manager(window, tier, session, llm=llm)
    log(trace, "setup", window=window, tier=tier,
        compression_threshold=manager.budget.compression_threshold,
        original_msg_count=len(session.messages),
        original_tokens_est=manager._estimate_messages_tokens(session.messages))

    # Directly exercise CompressionService to confirm no SKIPPED_SMALL floor.
    comp = asyncio.run(manager.compression_service.compress(session.messages, "s2"))
    log(trace, "compress_direct", status=comp.status.value,
        original_tokens=comp.original_tokens, compressed_tokens=comp.compressed_tokens,
        llm_calls=len(llm.calls))
    floor_gone = comp.status != CompressionStatus.SKIPPED_SMALL

    # Full pipeline: masking then compression, persisted to session.
    result, managed = asyncio.run(manager.manage(list(session.messages), "s2"))
    summary_in_session = any(
        "Previous Conversation Summary" in (m.get("content") or "")
        for m in session.messages
    )
    log(trace, "result", floor_gone=floor_gone,
        compressed=result.compressed, truncated=result.truncated,
        final_tokens=result.final_tokens,
        summary_persisted=summary_in_session,
        session_msg_count=len(session.messages))
    path = write_trace(name, trace)
    ok = floor_gone and result.compressed and summary_in_session
    print(f"[{name}] {'PASS' if ok else 'FAIL'} floor_gone={floor_gone} compressed={result.compressed} persisted={summary_in_session} trace={path}")
    return ok


# ---------------------------------------------------------------------------
# Scenario 3: Masking persists across "turns" (Bug 3 — read-only main path)
# ---------------------------------------------------------------------------
def scenario_masking_persists_across_turns() -> bool:
    name = "03_masking_persists_across_turns"
    trace: List[Dict[str, Any]] = []
    window, tier = 32768, "small"
    session = FakeSession("s3")
    session.messages.append(tool_msg("bash", 16000))
    session.messages.append(user_msg("run it"))
    session.messages.append(assistant_msg("done"))

    manager = make_manager(window, tier, session, llm=FakeLLM())

    # Turn 1: manage() should mask AND write back to session.messages.
    _, managed1 = asyncio.run(manager.manage(session.messages, "s3"))
    turn1_persisted = any(
        MASKING_INDICATOR in (m.get("content") or "") for m in session.messages
    )
    log(trace, "turn1", managed_equals_session=managed1 == session.messages,
        session_persisted=turn1_persisted, session_msg_count=len(session.messages))

    # Turn 2: a fresh manage() on the SAME session must see the already-masked
    # history (not reload a bloated copy) and not re-mask / not lose state.
    result2, managed2 = asyncio.run(manager.manage(session.messages, "s3"))
    turn2_still_masked = any(
        MASKING_INDICATOR in (m.get("content") or "") for m in session.messages
    )
    log(trace, "turn2", masked_again=result2.masked,
        still_has_placeholder=turn2_still_masked,
        final_tokens=result2.final_tokens, session_msg_count=len(session.messages))
    path = write_trace(name, trace)
    ok = turn1_persisted and turn2_still_masked
    print(f"[{name}] {'PASS' if ok else 'FAIL'} turn1_persisted={turn1_persisted} turn2_still_masked={turn2_still_masked} trace={path}")
    return ok


def main() -> int:
    print("=" * 60)
    print("  Plan 02 sandbox: Engine 1 masking & compaction fixes")
    print("=" * 60)
    checks = [
        scenario_masking_before_compression(),
        scenario_compression_small_window(),
        scenario_masking_persists_across_turns(),
    ]
    passed = sum(checks)
    print("-" * 60)
    print(f"sandbox: {passed}/{len(checks)} passed")
    print(f"traces written to {TRACE_DIR}/")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
