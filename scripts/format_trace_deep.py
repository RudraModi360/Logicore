"""Deep-dive trace formatter — full fidelity for debugging.

Produces a per-turn, per-iteration view showing EXACTLY what the model
sees at each request (context), what it thought, what tools it called,
and the FULL results — nothing truncated except the repeated system prompt.

Usage:
    python scripts/format_trace_deep.py context_analysis/traces/scenario_1/session_*.jsonl
    python scripts/format_trace_deep.py --all
"""
import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional


def _parse_tool_call_string(tc_str: str) -> Dict:
    """Parse 'function=Function(name=..., arguments={...})' into {name, arguments}."""
    m = re.search(r"name='([^']+)'", tc_str)
    name = m.group(1) if m else "?"
    m2 = re.search(r"arguments=(\{.*\})", tc_str, re.DOTALL)
    args = {}
    if m2:
        try:
            args = eval(m2.group(1), {"__builtins__": {}})  # safe: only literals
        except Exception:
            args = {"raw": m2.group(1)}
    return {"name": name, "arguments": args}


def _load_jsonl(path: str) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _system_prompt_placeholder(text: str) -> str:
    """Replace the big repeated system prompt with a placeholder."""
    n = len(str(text or ""))
    if n > 2000:
        return f"[SYSTEM PROMPT PLACEHOLDER — {n} chars, identical on every request — omitted]"
    return text  # small system messages (reasoning hints etc) shown in full


def format_deep(jsonl_path: str, fallback_model: str = "gemma4:cloud",
                fallback_provider: str = "ollama") -> Dict:
    records = _load_jsonl(jsonl_path)

    # ── collect session metadata ──────────────────────────────────────
    session_meta: Dict[str, Any] = {}
    for rec in records:
        if rec.get("type") == "session_start":
            provider_obj_model = rec.get("model") or fallback_model
            session_meta = {
                "provider": rec.get("provider") or fallback_provider,
                "model": provider_obj_model,
                "max_iterations": rec.get("max_iterations"),
                "tool_count": rec.get("tool_count"),
                "tool_names": _tool_names(rec.get("tools")),
                "system_prompt_chars": len(str(rec.get("system_prompt", ""))),
            }
            break

    # ── group records into turns ──────────────────────────────────────
    turns: List[Dict] = []
    cur_turn: Optional[Dict] = None
    cur_iter: Optional[Dict] = None  # current iteration inside a turn

    for rec in records:
        rtype = rec.get("type")

        # ── TURN START ────────────────────────────────────────────────
        if rtype == "turn_start":
            cur_turn = {
                "turn": rec.get("turn"),
                "user_input": rec.get("user_input"),
                "iterations": [],
                "final_response": None,
                "tokens_start": None,
                "tokens_end": None,
                "tool_call_sequence": [],
            }
            turns.append(cur_turn)
            cur_iter = None

        # ── LLM REQUEST (what model sees this iteration) ─────────────
        elif rtype == "llm_request":
            messages = rec.get("messages", [])
            cur_iter = {
                "iteration": rec.get("iteration"),
                "est_tokens": rec.get("est_tokens"),
                "message_count": rec.get("message_count"),

                # ── CONTEXT: exactly what is sent to the model ───────
                "context": {
                    "total_messages": len(messages),
                    "total_chars": sum(m.get("chars", 0) for m in messages),
                    "messages": [
                        _format_context_message(m) for m in messages
                    ],
                },

                # ── MODEL OUTPUT (filled by later records) ───────────
                "thinking": None,
                "model_content": None,
                "tool_call_intents": [],  # what model WANTED to call (name+args)
                "tool_calls": [],         # what actually EXECUTED (name+args+result)
                "streamed_text": None,
            }
            if cur_turn is not None:
                cur_turn["iterations"].append(cur_iter)
                if cur_turn["tokens_start"] is None:
                    cur_turn["tokens_start"] = rec.get("est_tokens")

        # ── MODEL RESPONSE (content + tool_calls + reasoning) ───────
        elif rtype == "model_response":
            if cur_iter is not None:
                content = rec.get("content") or ""
                reasoning = rec.get("reasoning") or ""
                usage = rec.get("usage") or {}

                cur_iter["model_content"] = content if content else None
                if reasoning:
                    cur_iter["thinking"] = reasoning

                # extract tool call intents (name + args only — result comes from tool_call record)
                raw_tcs = rec.get("tool_calls") or []
                for tc in raw_tcs:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        cur_iter["tool_call_intents"].append({
                            "name": fn.get("name", "?"),
                            "arguments": fn.get("arguments", {}),
                        })
                    elif isinstance(tc, str):
                        # format: "function=Function(name='web_search', arguments={...})"
                        parsed = _parse_tool_call_string(tc)
                        cur_iter["tool_call_intents"].append(parsed)

                # token usage
                if usage:
                    cur_iter["token_usage"] = usage

        # ── TOOL CALL RESULT (full result, not truncated) ────────────
        elif rtype == "tool_call":
            if cur_iter is not None:
                result = rec.get("result")
                cur_iter["tool_calls"].append({
                    "name": rec.get("name"),
                    "arguments": rec.get("args"),
                    "success": rec.get("success"),
                    "result": result,  # FULL, no truncation
                })
                if cur_turn is not None:
                    cur_turn["tool_call_sequence"].append(rec.get("name"))

        # ── THINKING (reasoning deltas accumulated) ──────────────────
        elif rtype == "thinking":
            if cur_iter is not None:
                # accumulate if multiple thinking records for same iteration
                existing = cur_iter.get("thinking") or ""
                new_text = rec.get("text") or ""
                cur_iter["thinking"] = (existing + new_text) if existing else new_text

        # ── STREAMED TEXT (what was streamed to output) ──────────────
        elif rtype == "streamed_text":
            if cur_iter is not None:
                cur_iter["streamed_text"] = rec.get("text")

        # ── TURN END (final response + token count) ──────────────────
        elif rtype == "turn_end":
            if cur_turn is not None:
                cur_turn["final_response"] = rec.get("final_response")
                cur_turn["tokens_end"] = rec.get("est_tokens")
            cur_iter = None

        # ── USAGE / ERROR ────────────────────────────────────────────
        elif rtype == "usage":
            if cur_iter is not None:
                cur_iter["stream_usage"] = rec.get("usage")
        elif rtype == "error":
            if cur_iter is not None:
                cur_iter["error"] = rec.get("message")

    # ── compute per-turn context growth summary ───────────────────────
    for turn in turns:
        growth = []
        for it in turn.get("iterations", []):
            growth.append({
                "iteration": it["iteration"],
                "est_tokens": it["est_tokens"],
                "messages": it["context"]["total_messages"],
                "chars": it["context"]["total_chars"],
            })
        turn["context_growth"] = growth

    return {
        "trace_file": os.path.basename(jsonl_path),
        **session_meta,
        "total_turns": len(turns),
        "total_tool_calls": sum(len(t.get("tool_call_sequence", [])) for t in turns),
        "turns": turns,
    }


def _format_context_message(m: Dict) -> Dict:
    """Format a single message in the context — keep content FULL."""
    role = m.get("role")
    content = m.get("content") or ""
    chars = m.get("chars", len(content))
    tool_calls = m.get("tool_calls")

    out: Dict[str, Any] = {
        "role": role,
        "chars": chars,
        "content": content,  # FULL — no truncation
    }

    # system prompt gets placeholder if huge
    if role == "system" and chars > 2000:
        out["content"] = _system_prompt_placeholder(content)
        out["is_placeholder"] = True

    # tool_calls from assistant messages — may be string format
    if tool_calls:
        parsed_tcs = []
        for tc in (tool_calls if isinstance(tool_calls, list) else [tool_calls]):
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                parsed_tcs.append({"name": fn.get("name", "?"),
                                   "arguments": fn.get("arguments", {})})
            elif isinstance(tc, str):
                parsed_tcs.append(_parse_tool_call_string(tc))
        out["tool_calls"] = parsed_tcs

    # tool_call_id for tool messages
    if m.get("tool_call_id"):
        out["tool_call_id"] = m["tool_call_id"]

    return out


def _tool_names(tools: Any) -> List[str]:
    if not tools:
        return []
    names = []
    for t in tools:
        if isinstance(t, dict):
            fn = t.get("function", {})
            names.append(fn.get("name", "?"))
        elif isinstance(t, str):
            import re
            m = re.search(r"'name':\s*'([^']+)'", t)
            names.append(m.group(1) if m else t[:50])
    return sorted(set(names))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--model", default="gemma4:cloud")
    p.add_argument("--provider", default="ollama")
    args = p.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.all:
        paths = glob.glob(os.path.join(
            root, "context_analysis", "traces", "scenario_*", "session_*.jsonl"))
    else:
        paths = args.paths

    if not paths:
        print("No traces found. Use --all or pass paths.")
        sys.exit(1)

    for path in paths:
        print(f"Formatting: {path}")
        result = format_deep(path, fallback_model=args.model,
                             fallback_provider=args.provider)
        out_path = path.replace(".jsonl", "_deep.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        kb = os.path.getsize(out_path) / 1024
        print(f"  -> {out_path}  ({kb:.0f} KB)")
        print(f"     turns={result['total_turns']}  "
              f"tool_calls={result['total_tool_calls']}  "
              f"model={result['model']}")


if __name__ == "__main__":
    main()
