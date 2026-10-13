"""
experiments/replay_context_bench.py
Replays historical trace JSONL files to benchmark the 3 context engines.

Standalone by design: it does NOT import logicore (sandbox phase - Plan 00).
It reads the exact message snapshots that were sent to the LLM (event type
`llm_request`) and re-runs them through simulated versions of:

  Engine 1 - Masking        : 60% context-window trigger, tool outputs > 2000
                              chars spill to %TEMP%/logicore_masked/<session_id>/
                              and are replaced by an [OUTPUT_MASKED] stub.
  Engine 1 - Summarization  : 80% context-window trigger, older turns collapsed
                              into a single digest message. NO 100k hard floor.
  Engine 3 - Serialization  : redundant raw tool JSON envelope stripped; when
                              reused_cached_result is true the payload collapses
                              to a tiny [CACHED_RESULT] stub.

Usage:
    python experiments/replay_context_bench.py
    python experiments/replay_context_bench.py --trace debug/traces/session_20261009-143119_320d2c.jsonl
    python experiments/replay_context_bench.py --all
    python experiments/replay_context_bench.py --context-window 64000 --min-reduction 60

Acceptance metric
-----------------
The gate is measured on the *managed* portion of *bloated* requests - i.e.
llm_request snapshots whose context already breached the 60% masking trigger,
excluding the system prompt (Plan 05 scope) and walkthrough pollution
(Plan 04 scope). Healthy turns that never breach the trigger are reported
separately as the "overall" number.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

MASKING_INDICATOR = "[OUTPUT_MASKED]"
CACHED_INDICATOR = "[CACHED_RESULT]"
SUMMARY_INDICATOR = "[SUMMARY]"

MASK_THRESHOLD_RATIO = 0.60
COMPRESS_THRESHOLD_RATIO = 0.80
MASK_MIN_CHARS = 2000          # only spill tool payloads bigger than this
PROTECT_LAST_MESSAGES = 2      # never mask/compress the newest exchange
PRESERVE_RECENT_COUNT = 10     # compression keeps the last N messages verbatim
SUMMARY_DIGEST_CHARS = 600     # size of the synthetic digest message
DEFAULT_TRACE = os.path.join(
    "context_analysis", "traces", "scenario_1", "session_20261010-103025_011fff.jsonl"
)


# --------------------------------------------------------------------------- #
# Token counting (heuristic, same ~4 chars/token rule as logicore's estimator)
# --------------------------------------------------------------------------- #
def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // 4


def message_tokens(msg: Dict[str, Any]) -> int:
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return 4 + count_tokens(content)  # 4 token role overhead


def messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(message_tokens(m) for m in messages)


WALKTHROUGH_MARKERS = ("Walkthrough Summary", "Task execution is complete")


def categorize_tokens(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    """Split context tokens into: system / tool / walkthrough / chat.

    The system prompt and walkthrough pollution are addressed by Plan 05 and
    Plan 04 respectively - they are OUT OF SCOPE for Engines 1-3, so the bench
    tracks them separately ("managed" context = everything else).
    """
    out = {"system": 0, "tool": 0, "walkthrough": 0, "chat": 0}
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tokens = 4 + count_tokens(content)
        if role == "system":
            out["system"] += tokens
        elif role == "tool":
            out["tool"] += tokens
        elif any(marker in content for marker in WALKTHROUGH_MARKERS):
            out["walkthrough"] += tokens
        else:
            out["chat"] += tokens
    return out


# --------------------------------------------------------------------------- #
# Trace loading
# --------------------------------------------------------------------------- #
def load_events(trace_path: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with open(trace_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate partially flushed lines
    return events


def extract_requests(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reqs = [e for e in events if e.get("type") == "llm_request" and e.get("messages")]
    # keep chronological order (seq when present, else file order)
    return sorted(reqs, key=lambda e: e.get("seq", 0))


# --------------------------------------------------------------------------- #
# Engine 3 - serialization / distillation
# --------------------------------------------------------------------------- #
def distill_tool_messages(messages: List[Dict[str, Any]], stats: Dict[str, int]) -> List[Dict[str, Any]]:
    """Strip redundant raw tool JSON envelopes; collapse reused cached results."""
    out: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") != "tool" or not isinstance(content, str) or not content:
            out.append(msg)
            continue
        if content.startswith(MASKING_INDICATOR) or content.startswith(CACHED_INDICATOR):
            out.append(msg)
            continue

        parsed: Optional[Dict[str, Any]] = None
        try:
            candidate = json.loads(content)
            if isinstance(candidate, dict):
                parsed = candidate
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if parsed is None:
            out.append(msg)
            continue

        if parsed.get("reused_cached_result") is True:
            stats["cached_hits"] += 1
            name = str(parsed.get("tool", "tool"))
            stub = (
                f"{CACHED_INDICATOR}\n"
                f"Tool: {name}\n"
                f"reused_cached_result: true (identical request already executed "
                f"earlier in this session; payload omitted)"
            )
            out.append({**msg, "content": stub})
            stats["distilled"] += 1
            continue

        inner = parsed.get("content")
        if isinstance(inner, str) and inner:
            envelope_tokens = count_tokens(content)
            new_content = inner
            if parsed.get("success") is False:
                err = parsed.get("error") or parsed.get("message") or "tool failed"
                new_content = f"ERROR: {err}\n{inner}"
            out.append({**msg, "content": new_content})
            stats["distilled"] += 1
            stats["distill_tokens_saved"] += max(0, envelope_tokens - count_tokens(new_content))
            continue

        out.append(msg)
    return out


# --------------------------------------------------------------------------- #
# Engine 1 - masking (60% trigger, disk spill)
# --------------------------------------------------------------------------- #
def _spill_path(session_id: str, tool_name: str, seq: int) -> str:
    safe_session = re.sub(r"[^\w\-]", "_", session_id)[:50] or "default"
    safe_tool = re.sub(r"[^\w\-]", "_", tool_name)[:50] or "tool"
    root = os.path.join(tempfile.gettempdir(), "logicore_masked", safe_session)
    os.makedirs(root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(root, f"{safe_tool}_{stamp}_{seq}.txt")


def mask_tool_outputs(
    messages: List[Dict[str, Any]],
    session_id: str,
    mask_threshold: int,
    stats: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Mask every prunable tool output once the context breaches the threshold.

    Mirrors ToolOutputMaskingService: the 60% window breach is the *trigger*;
    once triggered, all tool payloads > MASK_MIN_CHARS outside the protection
    window are offloaded to disk (FIFO, oldest first) - not just enough to get
    back under budget.
    """
    msgs = list(messages)
    if messages_tokens(msgs) <= mask_threshold:
        return msgs

    protect_from = max(0, len(msgs) - PROTECT_LAST_MESSAGES)
    candidates = [
        i
        for i in range(protect_from)
        if msgs[i].get("role") == "tool"
        and isinstance(msgs[i].get("content"), str)
        and len(msgs[i]["content"]) > MASK_MIN_CHARS
        # startswith (not `in`): trace payloads may quote the literal marker
        and not msgs[i]["content"].startswith(MASKING_INDICATOR)
        and not msgs[i]["content"].startswith(CACHED_INDICATOR)
    ]

    for idx in candidates:
        original = msgs[idx]["content"]
        tool_name = str(msgs[idx].get("name") or "tool")
        path = _spill_path(session_id, tool_name, stats["spill_files"] + 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)

        placeholder = (
            f"{MASKING_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Output size: {len(original)} chars (~{count_tokens(original)} tokens), "
            f"{original.count(chr(10)) + 1} lines\n"
            f"Full output saved to: {path}\n"
            f"Reason: Output masked to preserve context window"
        )
        saved = count_tokens(original) - count_tokens(placeholder)
        msgs[idx] = {**msgs[idx], "content": placeholder}
        stats["masked"] += 1
        stats["mask_tokens_saved"] += max(0, saved)
        stats["spill_files"] += 1
        stats["spill_paths"].append(path)
    return msgs


# --------------------------------------------------------------------------- #
# Engine 1 - summarization (80% trigger, no 100k floor)
# --------------------------------------------------------------------------- #
def compress_old_turns(
    messages: List[Dict[str, Any]],
    compress_threshold: int,
    stats: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Collapse older history into a single digest, preserving the newest N messages.

    Mirrors CompressionService: once the 80% trigger is breached, everything
    older than `PRESERVE_RECENT_COUNT` (system prompts excluded) is summarized.
    No absolute 100k floor - the trigger scales with the model context window.
    """
    msgs = list(messages)
    guard = 0
    while messages_tokens(msgs) > compress_threshold and guard < 50:
        guard += 1
        system_idx = [i for i, m in enumerate(msgs) if m.get("role") == "system"]
        first_live = (max(system_idx) + 1) if system_idx else 0
        split = max(first_live, len(msgs) - PRESERVE_RECENT_COUNT)
        if split <= first_live:
            break  # nothing safely compressible left

        chunk = msgs[first_live:split]
        chunk_tokens = sum(message_tokens(m) for m in chunk)
        digest_body = ""
        for m in chunk:
            c = m.get("content", "")
            if not isinstance(c, str):
                c = json.dumps(c, ensure_ascii=False)
            digest_body += f"[{m.get('role', '?')}] {c}\n"
        digest_body = digest_body.strip()
        if len(digest_body) > SUMMARY_DIGEST_CHARS:
            digest_body = digest_body[:SUMMARY_DIGEST_CHARS] + " ...[truncated]"

        digest_msg = {
            "role": "user",
            "content": (
                f"{SUMMARY_INDICATOR} Earlier conversation auto-compressed "
                f"({len(chunk)} messages, ~{chunk_tokens} tokens) to free context budget.\n"
                f"{digest_body}"
            ),
        }
        before = messages_tokens(msgs)
        msgs = msgs[:first_live] + [digest_msg] + msgs[split:]
        after = messages_tokens(msgs)
        if after >= before:
            break  # compression is not paying off - stop
        stats["compressions"] += 1
        stats["compress_tokens_saved"] += max(0, chunk_tokens - message_tokens(digest_msg))
    return msgs


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(
    messages: List[Dict[str, Any]],
    context_window: int,
    session_id: str,
) -> Dict[str, Any]:
    mask_threshold = int(context_window * MASK_THRESHOLD_RATIO)
    compress_threshold = int(context_window * COMPRESS_THRESHOLD_RATIO)

    stats = {
        "distilled": 0,
        "distill_tokens_saved": 0,
        "masked": 0,
        "mask_tokens_saved": 0,
        "spill_files": 0,
        "spill_paths": [],
        "cached_hits": 0,
        "compressions": 0,
        "compress_tokens_saved": 0,
    }

    original = messages_tokens(messages)

    stage_distill = distill_tool_messages(messages, stats)
    after_distill = messages_tokens(stage_distill)

    stage_mask = mask_tool_outputs(stage_distill, session_id, mask_threshold, stats)
    after_mask = messages_tokens(stage_mask)

    stage_compress = compress_old_turns(stage_mask, compress_threshold, stats)
    final = messages_tokens(stage_compress)

    return {
        "original": original,
        "after_distill": after_distill,
        "after_mask": after_mask,
        "final": final,
        "saved": max(0, original - final),
        "stats": stats,
        "messages": stage_compress,
        "mask_threshold": mask_threshold,
        "compress_threshold": compress_threshold,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_table(rows: List[Dict[str, Any]]) -> None:
    headers = ["Turn", "Original Tokens", "Masked Tokens", "Tokens Saved", "Disk Spill Files"]
    body = [
        [
            str(r["turn"]) + ("*" if r.get("bloated") else ""),
            f"{r['original']:,}",
            f"{r['final']:,}",
            f"{r['saved']:,} ({r['pct']}%)",
            r["spill"],
        ]
        for r in rows
    ]
    widths = [max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(headers)]
    sep = "-----+" + "+".join("-" * (w + 2) for w in widths)

    def fmt(cells: List[str]) -> str:
        out = cells[0].ljust(widths[0])
        for i in range(1, len(cells)):
            out += " | " + cells[i].rjust(widths[i])
        return out

    print(fmt(headers))
    print(sep)
    for b in body:
        print(fmt(b))


def benchmark_trace(trace_path: str, context_window: int, verbose: bool = True) -> Dict[str, Any]:
    events = load_events(trace_path)
    requests = extract_requests(events)
    if not requests:
        return {
            "trace": trace_path, "session_id": "unknown", "requests": 0, "turns": 0,
            "rows": [], "original": 0, "final": 0, "reduction": 0.0,
            "bloated_requests": 0, "bloated_original": 0, "bloated_final": 0,
            "bloated_reduction": 0.0, "bloated_managed_original": 0,
            "bloated_managed_final": 0, "bloated_managed_reduction": 0.0, "totals": {},
        }

    session_id = "unknown"
    for ev in events:
        if ev.get("session_id"):
            session_id = str(ev["session_id"])
            break
    session_id = f"{session_id}_{os.path.splitext(os.path.basename(trace_path))[0]}"

    # One representative row per turn = the fattest request of that turn.
    per_turn: Dict[int, Dict[str, Any]] = {}
    for req in requests:
        turn = int(req.get("turn") or 0)
        tokens = messages_tokens(req["messages"])
        if turn not in per_turn or tokens > per_turn[turn]["original"]:
            per_turn[turn] = {"turn": turn, "messages": req["messages"], "original": tokens}

    rows: List[Dict[str, Any]] = []
    cum_spill = 0
    total_original = 0
    total_final = 0
    bloated_original = 0
    bloated_final = 0
    bloated_managed_original = 0
    bloated_managed_final = 0
    bloated_requests = 0
    mask_threshold = int(context_window * MASK_THRESHOLD_RATIO)
    fattest_bloated = None  # (original_tokens, category_breakdown)
    totals = {
        "distilled": 0,
        "masked": 0,
        "cached_hits": 0,
        "compressions": 0,
        "distill_tokens_saved": 0,
        "mask_tokens_saved": 0,
        "compress_tokens_saved": 0,
        "spill_files": 0,
    }

    for turn in sorted(per_turn):
        messages = per_turn[turn]["messages"]
        result = run_pipeline(messages, context_window, session_id)
        st = result["stats"]
        cats = categorize_tokens(messages)
        system_tokens = cats["system"]
        managed_original = max(0, result["original"] - system_tokens)
        managed_final = max(0, result["final"] - system_tokens)
        cum_spill += st["spill_files"]
        total_original += result["original"]
        total_final += result["final"]
        is_bloated = result["original"] > mask_threshold
        if is_bloated:
            bloated_requests += 1
            bloated_original += result["original"]
            bloated_final += result["final"]
            bloated_managed_original += managed_original
            bloated_managed_final += managed_final
            if fattest_bloated is None or result["original"] > fattest_bloated[0]:
                fattest_bloated = (result["original"], cats)
        for k in totals:
            totals[k] += st.get(k, 0)
        rows.append(
            {
                "turn": turn,
                "original": result["original"],
                "final": result["final"],
                "saved": result["saved"],
                "pct": round(100.0 * result["saved"] / result["original"], 1) if result["original"] else 0.0,
                "spill": f"{cum_spill} files written",
                "bloated": is_bloated,
            }
        )

    reduction = (100.0 * (total_original - total_final) / total_original) if total_original else 0.0
    bloated_reduction = (
        100.0 * (bloated_original - bloated_final) / bloated_original if bloated_original else 0.0
    )
    bloated_managed_reduction = (
        100.0 * (bloated_managed_original - bloated_managed_final) / bloated_managed_original
        if bloated_managed_original
        else 0.0
    )

    if verbose:
        print(f"\n=== Benchmarking Trace: {os.path.basename(trace_path)} ===")
        print(f"Context Window Target : {context_window} tokens")
        print(f"Mask threshold (60%)  : {int(context_window * MASK_THRESHOLD_RATIO):,} tokens")
        print(f"Compress threshold(80%): {int(context_window * COMPRESS_THRESHOLD_RATIO):,} tokens")
        print(f"LLM requests replayed : {len(requests)} across {len(per_turn)} turn(s)")
        print()
        print_table(rows)
        print("  (* turn breached the 60% masking trigger - engines engaged)")
        print()
        print("Engine breakdown:")
        print(f"  Engine 3 (serialization) : {totals['distilled']} tool payloads distilled, "
              f"{totals['cached_hits']} reused_cached_result=True, "
              f"~{totals['distill_tokens_saved']:,} tokens saved")
        print(f"  Engine 1 (masking)       : {totals['masked']} outputs spilled to disk, "
              f"~{totals['mask_tokens_saved']:,} tokens saved")
        print(f"  Engine 1 (summarization) : {totals['compressions']} compression passes, "
              f"~{totals['compress_tokens_saved']:,} tokens saved")
        spill_root = os.path.join(tempfile.gettempdir(), "logicore_masked", session_id)
        print(f"  Disk spill directory      : {spill_root}")
        print()
        print(f"TOTAL original : {total_original:,} tokens")
        print(f"TOTAL optimized: {total_final:,} tokens")
        print(f"TOTAL saved    : {total_original - total_final:,} tokens ({reduction:.1f}% reduction)")
        print(f"Bloated turns  : {bloated_requests} request(s) breached the 60% trigger -> "
              f"{bloated_reduction:.1f}% all-in / {bloated_managed_reduction:.1f}% managed "
              f"(managed = context minus system prompt)")
        if fattest_bloated:
            _, cats = fattest_bloated
            peak_total = sum(cats.values()) or 1
            owners = {
                "system": "Plan 05 target",
                "tool": "Engines 1/3 target",
                "walkthrough": "Plan 04 target",
                "chat": "Engines 1/3 target",
            }
            print("Fattest bloated context composition:")
            for name in ("system", "tool", "walkthrough", "chat"):
                print(f"    {name:<11}: {cats[name]:>7,} tokens "
                      f"({100.0 * cats[name] / peak_total:4.1f}%)   <- {owners[name]}")

    return {
        "trace": trace_path,
        "session_id": session_id,
        "requests": len(requests),
        "turns": len(per_turn),
        "rows": rows,
        "original": total_original,
        "final": total_final,
        "reduction": reduction,
        "bloated_requests": bloated_requests,
        "bloated_original": bloated_original,
        "bloated_final": bloated_final,
        "bloated_reduction": bloated_reduction,
        "bloated_managed_original": bloated_managed_original,
        "bloated_managed_final": bloated_managed_final,
        "bloated_managed_reduction": bloated_managed_reduction,
        "totals": totals,
    }


def find_default_traces() -> List[str]:
    patterns = [
        os.path.join("context_analysis", "traces", "**", "*.jsonl"),
        os.path.join("debug", "traces", "*.jsonl"),
    ]
    found: List[str] = []
    for pat in patterns:
        found.extend(glob.glob(pat, recursive=True))
    return sorted({p for p in found if os.path.getsize(p) > 0})


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay trace JSONL files through simulated context engines.")
    parser.add_argument("--trace", default=None, help="Path to a single .jsonl trace")
    parser.add_argument("--all", action="store_true", help="Run over every non-empty trace in the repo")
    parser.add_argument("--context-window", type=int, default=32768,
                        help="Simulated model context window (default 32768 = small tier)")
    parser.add_argument("--min-reduction", type=float, default=60.0, help="Acceptance threshold (percent)")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary verdict")
    args = parser.parse_args(argv)

    corpus = find_default_traces()
    if args.all:
        traces = corpus
        verbose_everywhere = True
    elif args.trace:
        traces = [args.trace]
        verbose_everywhere = True
    else:
        # Default: deep-dive table on the Plan 00 acceptance trace, then the
        # rest of the corpus (quiet) so the exit-code verdict is corpus-wide.
        traces = [DEFAULT_TRACE] + [
            p for p in corpus if os.path.abspath(p) != os.path.abspath(DEFAULT_TRACE)
        ]
        verbose_everywhere = False

    if not traces:
        print("No traces found.", file=sys.stderr)
        return 2

    results = []
    for idx, path in enumerate(traces):
        if not os.path.exists(path):
            print(f"Trace not found: {path}", file=sys.stderr)
            return 2
        verbose = verbose_everywhere or (idx == 0)
        results.append(benchmark_trace(path, args.context_window, verbose=verbose and not args.quiet))

    usable = [r for r in results if r["requests"]]
    if not usable:
        print("No llm_request events found in the selected trace(s).", file=sys.stderr)
        return 2

    total_original = sum(r["original"] for r in usable)
    total_final = sum(r["final"] for r in usable)
    reduction = (100.0 * (total_original - total_final) / total_original) if total_original else 0.0

    bloated_original = sum(r["bloated_original"] for r in usable)
    bloated_final = sum(r["bloated_final"] for r in usable)
    bloated_requests = sum(r["bloated_requests"] for r in usable)
    bloated_reduction = (
        100.0 * (bloated_original - bloated_final) / bloated_original if bloated_original else 0.0
    )
    bloated_managed_original = sum(r["bloated_managed_original"] for r in usable)
    bloated_managed_final = sum(r["bloated_managed_final"] for r in usable)
    bloated_managed_reduction = (
        100.0 * (bloated_managed_original - bloated_managed_final) / bloated_managed_original
        if bloated_managed_original
        else 0.0
    )

    if len(results) > 1:
        print("\n=== Per-trace summary ===")
        for r in results:
            if r["requests"]:
                print(f"  {os.path.basename(r['trace']):50s} overall {r['reduction']:5.1f}% "
                      f"({r['original']:,} -> {r['final']:,})  |  bloated turns: "
                      f"{r['bloated_requests']:2d} -> all-in {r['bloated_reduction']:5.1f}% / "
                      f"managed {r['bloated_managed_reduction']:5.1f}%")
            else:
                print(f"  {os.path.basename(r['trace']):50s} (skipped: no llm_request events)")

    print(f"\nOVERALL reduction (all turns): {reduction:.1f}% "
          f"({total_original:,} -> {total_final:,} tokens) across {len(usable)} trace(s)")
    print(f"BLOATED all-in reduction (past the 60% trigger): {bloated_reduction:.1f}% "
          f"({bloated_original:,} -> {bloated_final:,} tokens, {bloated_requests} request(s))")
    print(f"BLOATED managed reduction (system prompt excluded): {bloated_managed_reduction:.1f}% "
          f"({bloated_managed_original:,} -> {bloated_managed_final:,} tokens)")
    print("  Note: the ~44k-char system prompt and walkthrough pollution are untouched by")
    print("  Engines 1-3 by design - they are the targets of Plan 05 and Plan 04.")

    if bloated_requests == 0:
        print(f"INCONCLUSIVE: no request breached the 60% masking trigger at a "
              f"{args.context_window:,}-token window. Re-run with a smaller window "
              f"(e.g. --context-window 16384) or with --all.")
        return 2

    if bloated_managed_reduction >= args.min_reduction:
        print(f"PASS (>= {args.min_reduction:.0f}% token reduction on the managed portion of "
              f"bloated contexts via masking, compression & serialization)")
        return 0
    print(f"FAIL (needed >= {args.min_reduction:.0f}% managed reduction on bloated contexts, "
          f"got {bloated_managed_reduction:.1f}%)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
