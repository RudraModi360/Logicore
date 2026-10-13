"""Format raw JSONL session traces into clean, debug-friendly JSON.

Usage:
    python scripts/format_trace.py context_analysis/traces/scenario_1/session_*.jsonl
    python scripts/format_trace.py --all   # formats all scenario traces

Output: <trace_name>_formatted.json next to the source file.
System prompt is replaced with a short placeholder for readability.
"""
import argparse
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional


def _clip(text: Any, limit: int = 1500) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[+{len(s) - limit} chars truncated]"


def _tool_call_pattern(events: List[Dict]) -> List[str]:
    """Extract the sequence of tool names called in order."""
    pattern = []
    for ev in events:
        if ev.get("type") == "model_response":
            for tc in ev.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                elif isinstance(tc, str):
                    name = tc
                else:
                    name = str(tc)
                pattern.append(name)
    return pattern


def format_trace(jsonl_path: str, fallback_model: str = None, fallback_provider: str = None) -> Dict:
    with open(jsonl_path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    session_meta: Dict[str, Any] = {}
    turns: List[Dict] = []
    current_turn: Optional[Dict] = None
    current_iterations: List[Dict] = None
    current_iteration: Optional[Dict] = None

    for rec in records:
        rtype = rec.get("type")

        if rtype == "session_start":
            session_meta = {
                "provider": rec.get("provider") or fallback_provider,
                "model": rec.get("model") or fallback_model,
                "max_iterations": rec.get("max_iterations"),
                "tool_count": rec.get("tool_count"),
                "tool_names": _extract_tool_names(rec.get("tools")),
                "system_prompt": _clip(rec.get("system_prompt", ""), 200)
                    + (f"\n... [SYSTEM PROMPT PLACEHOLDER — full text omitted, "
                       f"{len(str(rec.get('system_prompt', '')))} chars total]"),
            }

        elif rtype == "turn_start":
            current_turn = {
                "turn": rec.get("turn"),
                "user_input": rec.get("user_input"),
                "iterations": [],
                "final_response": None,
                "tokens_start": None,
                "tokens_end": None,
                "tool_call_pattern": [],
                "total_tool_calls": 0,
            }
            current_iterations = current_turn["iterations"]
            turns.append(current_turn)
            current_iteration = None

        elif rtype == "llm_request":
            current_iteration = {
                "iteration": rec.get("iteration"),
                "message_count": rec.get("message_count"),
                "est_tokens": rec.get("est_tokens"),
                "tool_count": rec.get("tool_count"),
                "tools_available": rec.get("tools"),  # just names list
                "messages_summary": _summarize_messages(rec.get("messages")),
                "model_response": None,
                "tool_calls": [],
                "thinking": None,
            }
            if current_iterations is not None:
                current_iterations.append(current_iteration)
            if current_turn and current_turn["tokens_start"] is None:
                current_turn["tokens_start"] = rec.get("est_tokens")

        elif rtype == "model_response":
            if current_iteration is not None:
                current_iteration["model_response"] = {
                    "content": _clip(rec.get("content"), 800),
                    "tool_calls_raw": _extract_tool_calls(rec.get("tool_calls")),
                    "usage": rec.get("usage"),
                }
                # If token stream ended, update tokens_end
                if current_turn:
                    # approximate from usage if present
                    usage = rec.get("usage") or {}
                    if isinstance(usage, dict):
                        t = usage.get("total_tokens") or usage.get("total")
                        if t:
                            current_turn["tokens_end"] = t

        elif rtype == "tool_call":
            if current_iteration is not None:
                tool_rec = {
                    "name": rec.get("name"),
                    "args": rec.get("args"),
                    "success": rec.get("success"),
                    "result_preview": _clip(rec.get("result"), 600),
                }
                current_iteration["tool_calls"].append(tool_rec)
                if current_turn:
                    current_turn["total_tool_calls"] += 1

        elif rtype == "thinking":
            if current_iteration is not None:
                current_iteration["thinking"] = _clip(rec.get("text"), 1200)

        elif rtype == "turn_end":
            if current_turn is not None:
                current_turn["final_response"] = _clip(rec.get("final_response"), 3000)
                current_turn["tokens_end"] = rec.get("est_tokens") or current_turn.get("tokens_end")
                current_turn["tool_call_pattern"] = _tool_call_pattern(
                    # reconstruct from iterations
                    [{"type": "model_response", "tool_calls": [
                        {"function": {"name": tc["name"]}}
                        for tc in (current_iteration or {}).get("tool_calls", [])
                    ]} for current_iteration in current_turn.get("iterations", [])
                     if current_iteration.get("tool_calls")]
                )
                # flatten pattern from all iterations
                pattern = []
                for it in current_turn.get("iterations", []):
                    for tc in it.get("tool_calls", []):
                        pattern.append(tc["name"])
                current_turn["tool_call_pattern"] = pattern
                current_iteration = None

    # Clean up: remove empty fields for readability
    for turn in turns:
        for it in turn.get("iterations", []):
            if it.get("model_response") and not it["model_response"].get("tool_calls_raw"):
                it["model_response"]["tool_calls_raw"] = None
            if not it.get("thinking"):
                it["thinking"] = None
            if not it.get("tool_calls"):
                it["tool_calls"] = []
        if not turn.get("tool_call_pattern"):
            turn["tool_call_pattern"] = []

    return {
        "trace_file": os.path.basename(jsonl_path),
        **session_meta,
        "total_turns": len(turns),
        "total_tool_calls": sum(t.get("total_tool_calls", 0) for t in turns),
        "turns": turns,
    }


def _extract_tool_names(tools: Any) -> List[str]:
    if not tools:
        return []
    names = []
    for t in tools:
        if isinstance(t, dict):
            fn = t.get("function", {})
            names.append(fn.get("name", "?"))
        elif isinstance(t, str):
            # format: "{'type': 'function', 'function': {'name': 'xxx', ...}}"
            import re
            m = re.search(r"'name':\s*'([^']+)'", t)
            names.append(m.group(1) if m else t[:50])
        else:
            names.append(str(t))
    return sorted(set(names))


def _extract_tool_calls(tool_calls: Any) -> List[Dict]:
    if not tool_calls:
        return []
    out = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            fn = tc.get("function", {})
            out.append({
                "name": fn.get("name", "?"),
                "arguments": fn.get("arguments", {}),
            })
        elif isinstance(tc, str):
            out.append({"raw": tc[:300]})
        else:
            out.append({"raw": str(tc)[:300]})
    return out


def _summarize_messages(messages: Any) -> List[Dict]:
    """Give a compact summary of each message (role, char count, snippet)."""
    if not messages:
        return []
    out = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "")
        entry = {
            "role": m.get("role"),
            "chars": len(content),
            "snippet": _clip(content, 150),
        }
        if m.get("tool_calls"):
            tc_names = []
            for tc in (m.get("tool_calls") or []):
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    tc_names.append(fn.get("name", "?"))
                else:
                    tc_names.append(str(tc)[:30])
            entry["tool_calls"] = tc_names
        out.append(entry)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="*", help="JSONL trace files to format")
    p.add_argument("--all", action="store_true", help="Format all scenario traces")
    p.add_argument("--model", default="gemma4:cloud", help="Fallback model name")
    p.add_argument("--provider", default="ollama", help="Fallback provider name")
    args = p.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.all:
        paths = glob.glob(os.path.join(
            root, "context_analysis", "traces", "scenario_*", "session_*.jsonl"))
    else:
        paths = args.paths

    if not paths:
        print("No trace files found. Use --all or pass paths.")
        sys.exit(1)

    for path in paths:
        print(f"Formatting: {path}")
        result = format_trace(path, fallback_model=args.model, fallback_provider=args.provider)
        out_path = path.replace(".jsonl", "_formatted.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  -> {out_path}")
        print(f"     turns={result.get('total_turns')} "
              f"tool_calls={result.get('total_tool_calls')} "
              f"model={result.get('model')}")


if __name__ == "__main__":
    main()
