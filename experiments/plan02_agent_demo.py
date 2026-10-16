"""
Plan 02 + real prototype agent — context management plugged in.

Wraps the ACTUAL prototype agent (experiments/prototype_agent.py) — its real
tools (bash/read_file/create_file/edit_file/web_search), ModelClient, and
run_turn() loop — and injects the Plan 02 context engine (FixedContextWindowManager)
in front of every LLM call. No fake tools, no fake sessions: masking and
compaction run against the live transcript as the agent really works.

How it plugs in:
  ContextManagedClient.chat()  ->  runs ctx.manage(session) first (masking/
  compression, persisted back into the same session list via a shim), then
  delegates to the real ModelClient. run_turn() is reused unchanged.

Usage:
    python experiments/plan02_agent_demo.py --model ollama/gemma4:cloud
    python experiments/plan02_agent_demo.py --model gemini/gemini-2.5-flash --context
    python experiments/plan02_agent_demo.py --model ... --context-length 16000 --model-tier small
    python experiments/plan02_agent_demo.py --model ... --no-context   # baseline

Runs an interactive terminal session: every turn is typed live at the
"You> " prompt. Type exit / quit / q (or Ctrl+C / Ctrl+D) to end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reuse the REAL prototype agent machinery (tools, client, loop, tracer).
from experiments import prototype_agent as _proto  # noqa: E402  (patched below)
from experiments.prototype_agent import (  # noqa: E402
    ModelClient,
    PrototypeTracer,
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    resolve_provider_model,
    run_turn,
)
from logicore.runtime.config import RuntimeConfig  # noqa: E402
from logicore.runtime.context.masking import MASKING_INDICATOR  # noqa: E402
from logicore.runtime.model_profile import ModelProfile  # noqa: E402
from experiments.context_engine import FixedContextWindowManager  # noqa: E402

import datetime as _dt  # noqa: E402


def tool_web_search_exa(args: Dict[str, Any]) -> str:
    """Exa-backed web_search with the prototype's (args)->str contract.

    The prototype's built-in scraper gets bot-tier ad junk from DDG/Bing
    (e.g. shoe ads for a cricket query). Exa returns real ranked results
    with text + dates. Falls back to the built-in scraper when no Exa key
    is configured or Exa errors, so search never disappears entirely.
    """
    query = (args.get("query") or "").strip()
    if not query:
        return "ERROR: empty query"
    num_results = int(args.get("num_results") or 5)

    def _fallback(reason: str) -> str:
        try:
            out = _proto.tool_web_search({"query": query})
        except Exception as exc:  # noqa: BLE001
            return f"web_search unavailable ({exc.__class__.__name__}); {reason}"
        return f"[fallback scraper: {reason}]\n{out}"

    try:
        from logicore.config.settings import get_api_key
        api_key = get_api_key("exa") or os.environ.get("EXA_API_KEY", "")
    except Exception:  # noqa: BLE001
        api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        return _fallback("EXA_API_KEY not set")

    import urllib.request as _req
    payload = json.dumps({
        "query": query,
        "type": "auto",
        "numResults": min(max(1, num_results), 10),
        "contents": {"text": True},
    }).encode("utf-8")
    try:
        rq = _req.Request(
            "https://api.exa.ai/search", data=payload,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
        )
        with _req.urlopen(rq, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"Exa error ({exc.__class__.__name__})")
    if isinstance(data, dict) and "error" in data:
        return _fallback(f"Exa API error ({data['error']})")
    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return f"No Exa results for: {query} — reformulate with different keywords and retry."
    lines = []
    for i, item in enumerate(results[:5], 1):
        title = item.get("title", "(untitled)")
        url = item.get("url", "")
        published = item.get("publishedDate", "")
        text = (item.get("text") or "")[:1200]
        lines.append(f"{i}. {title}" + (f" [{published}]" if published else "") + f"\n   {url}\n   {text}")
    return "\n".join(lines)


def _install_exa_search() -> str:
    """Route this demo's web_search through Exa; sharpen its schema text."""
    _proto.TOOL_IMPLS["web_search"] = tool_web_search_exa
    for entry in _proto.TOOL_SCHEMAS:
        fn = entry.get("function", {})
        if fn.get("name") == "web_search":
            fn["description"] = (
                "Search the live web (Exa) and return ranked results with dates. "
                "If results look irrelevant, reformulate with DIFFERENT keywords "
                "and retry — never repeat the identical query twice in a row."
            )
            props = fn.get("parameters", {}).get("properties", {})
            props["num_results"] = {"type": "integer"}
    return "exa" if (os.environ.get("EXA_API_KEY") or "") else "fallback-scraper"


def build_demo_system_prompt() -> str:
    """Base prototype prompt + general operating principles.

    Deliberately case-free: no mention of cricket, matplotlib, or any single
    tool. The agent follows the user's message as the spec, grounds every
    fact in tool output, and verifies before claiming done — whatever the task.
    """
    today = _dt.date.today().isoformat()
    return (
        SYSTEM_PROMPT
        + f"\nCurrent real-world date: {today}. Training data may be stale; tools outrank memory.\n"
        + "Operating principles:\n"
        + "A. The user's message is the spec. Do exactly what it asks — every part of it.\n"
        + "   If a request has multiple parts, complete ALL of them and confirm each one.\n"
        + "   Never substitute your own easier plan or silently drop a part.\n"
        + "B. Facts come from tools, never from memory. Look things up (search, read,\n"
        + "   inspect) BEFORE answering, and when tools contradict memory, tools win.\n"
        + "C. Verify before claiming done. Confirm artifacts exist (read/list them),\n"
        + "   check that numbers add up, and report paths/results only after confirmation.\n"
        + "D. On failure, change approach — different keywords, different tool, different\n"
        + "   angle — never the identical retry. Report failures honestly; never invent\n"
        + "   success or present guesses as verified facts.\n"
        + "E. Reconstructions and estimates must be labeled as such.\n"
        + "F. If the request is ambiguous, ask one short clarifying question OR state\n"
        + "   your interpretation up front — do not silently guess on important parts.\n"
        + "G. A user correction overrides everything before it: drop the old assumption\n"
        + "   and pursue the corrected request with fresh tool calls.\n"
    )


class SessionShim:
    """Expose the prototype's plain session list as a .messages session object."""

    def __init__(self, messages: List[Dict[str, Any]]):
        self.messages = messages  # same list the agent loop mutates


class AsyncLLM:
    """Adapt the sync ModelClient so CompressionService can await it."""

    def __init__(self, inner: ModelClient):
        self._inner = inner
        self.provider = getattr(inner, "provider", "unknown")
        self.model = getattr(inner, "model", "default")

    async def chat(self, messages, tools=None, max_tokens: int = 2048):
        return self._inner.chat(messages, tools=tools, max_tokens=max_tokens)


class ContextManagedClient:
    """Wrap ModelClient.chat to run Plan 02 context management first.

    run_turn() calls this before every LLM request. manage() masks/compresses
    the session (writing back via the shim), then the real client sends the
    compacted transcript to the model.
    """

    def __init__(self, inner: ModelClient, ctx: FixedContextWindowManager, session_id: str):
        self._inner = inner
        self._ctx = ctx
        self._session_id = session_id
        self.provider = getattr(inner, "provider", "")
        self.model = getattr(inner, "model", "")
        self.last_result: Optional[Any] = None

    def chat(self, messages, tools=None, max_tokens: int = 2048):
        result, _managed = asyncio.run(self._ctx.manage(messages, self._session_id))
        self.last_result = result
        if result.any_action_taken:
            flag = []
            if result.masked:
                flag.append(f"masked={result.masking_result.masked_count}")
            if result.compressed:
                flag.append("compressed")
            if result.truncated:
                flag.append("truncated")
            print(
                f"    [ctx] {result.original_tokens} -> {result.final_tokens} tokens "
                f"(saved {result.tokens_saved})  {', '.join(flag)}"
            )
        return self._inner.chat(messages, tools=tools, max_tokens=max_tokens)


def build_context_manager(profile: ModelProfile, model_client: ModelClient) -> FixedContextWindowManager:
    config = RuntimeConfig.from_settings()
    config.apply_model_profile(profile)
    # Scale the masking batch floor to the window. The framework default (10k)
    # is unreachable on small windows, which would leave masking dead there.
    config.context.min_prunable_tokens = max(
        500, min(config.context.min_prunable_tokens, profile.context_length // 8)
    )
    return FixedContextWindowManager(
        config=config,
        llm_provider=AsyncLLM(model_client),
        model_name=getattr(model_client, "model", "default"),
        # session_provider is wired per-call in main() once the session list exists.
        session_provider=None,
    )


class UsageRecorder:
    """Outermost client wrapper: records real provider usage per LLM call.

    ModelClient.chat returns {"content", "tool_calls", "usage"} where usage is
    the provider-reported dict (prompt/completion/cached tokens). run_turn()
    may call chat() several times per turn; main() slices self.calls per turn
    to report exact turn telemetry — no estimation involved.
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.provider = getattr(inner, "provider", "")
        self.model = getattr(inner, "model", "")
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, tools=None, max_tokens: int = 2048):
        resp = self._inner.chat(messages, tools=tools, max_tokens=max_tokens)
        self.calls.append(dict(resp.get("usage") or {}))
        return resp


def threshold_split(profile: ModelProfile, ctx: Optional[FixedContextWindowManager],
                    model_name: str = "default") -> Dict[str, int]:
    """Threshold variables, split from the given input (window/tier flags).

    Mirrors FixedTokenBudget.should_mask_tool_outputs exactly:
      mask_tool_at = min(protection_threshold_tokens, 35% of window)
    """
    window = profile.context_length
    if ctx is not None:
        compress_at = ctx.budget.compression_threshold
        c = ctx.config.context
        thres = {
            "window": window,
            "compress_at": compress_at,
            "mask_tool_at": min(c.protection_threshold_tokens, int(window * 0.35)),
            "protection": c.protection_threshold_tokens,
            "win35": int(window * 0.35),
            "min_prunable": c.min_prunable_tokens,
        }
    else:
        config = RuntimeConfig.from_settings()
        config.apply_model_profile(profile)
        c = config.context
        thres = {
            "window": window,
            "compress_at": config.get_compression_threshold_for_model(model_name),
            "mask_tool_at": min(c.protection_threshold_tokens, int(window * 0.35)),
            "protection": c.protection_threshold_tokens,
            "win35": int(window * 0.35),
            "min_prunable": c.min_prunable_tokens,
        }
    return thres


def _usage_sum(calls: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sum provider-reported usage dicts (OpenAI + Anthropic + Gemini shapes)."""
    prompt = completion = cached = 0
    for u in calls:
        prompt += u.get("prompt_tokens", 0) or 0
        completion += u.get("completion_tokens", 0) or 0
        det = u.get("prompt_tokens_details") or {}
        cached += (det.get("cached_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0)
    return {"prompt": prompt, "completion": completion, "cached": cached,
            "total": prompt + completion}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Plan 02 context engine on the real prototype agent")
    parser.add_argument("--model", default="gemma4:cloud", help="provider/model or bare name")
    parser.add_argument("--provider", default=None, help="gemini | groq | openai | ollama")
    parser.add_argument("--context-length", type=int, default=None, help="override context window")
    parser.add_argument("--model-tier", default=None, choices=["small", "standard", "medium", "large"])
    parser.add_argument("--context", dest="context", action="store_true", default=True,
                        help="enable Plan 02 context management (default)")
    parser.add_argument("--no-context", dest="context", action="store_false",
                        help="disable context management (baseline)")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--no-trace", dest="trace", action="store_false", default=True)
    args = parser.parse_args(argv)

    provider, model_name = resolve_provider_model(args.model, args.provider)
    model_config: Dict[str, Any] = {}
    if args.context_length is not None:
        model_config["context_length"] = args.context_length
    if args.model_tier is not None:
        model_config["model_tier"] = args.model_tier
    profile = ModelProfile.resolve(model_name, model_config or None)

    mode = "ON" if args.context else "OFF"
    print(f"PrototypeAgent+Plan02 | provider={provider} model={model_name} "
          f"context_management={mode}")
    print(f"Profile | tier={profile.tier} window={profile.context_length} "
          f"masking={profile.masking_ratio} compression={profile.compression_ratio}")

    try:
        model = ModelClient(provider, model_name)
    except ValueError as exc:
        print(f"Model client init failed: {exc}", file=sys.stderr)
        return 2

    # The live session: a plain list, same as the prototype agent.
    demo_prompt = build_demo_system_prompt()
    session: List[Dict[str, Any]] = [{"role": "system", "content": demo_prompt}]

    search_backend = _install_exa_search()
    print(f"web_search backend: {search_backend}")

    client: Any = model
    ctx: Optional[FixedContextWindowManager] = None
    if args.context:
        ctx = build_context_manager(profile, model)
        shim = SessionShim(session)
        ctx._session_provider = lambda sid: shim  # persist into the same list
        client = ContextManagedClient(model, ctx, "prototype")
    recorder = UsageRecorder(client)
    client = recorder

    thres = threshold_split(profile, ctx, model_name)
    print(f"[budget] window={thres['window']} compress_at={thres['compress_at']} "
          f"mask_tool_at={thres['mask_tool_at']} "
          f"(= min(protection={thres['protection']}, 35% window={thres['win35']})) "
          f"min_prunable={thres['min_prunable']}")

    tracer = PrototypeTracer(enabled=args.trace, provider=provider, model=model_name,
                             profile=profile.to_dict(),
                             trace_dir=os.path.join(_REPO_ROOT, "debug", "traces", "plan02"))
    tracer.session_start(demo_prompt, max_iterations=args.max_iterations)

    print("Type your turn live at the prompt below. exit / quit / q (or Ctrl+C) ends the session.")

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            break
        calls_before = len(recorder.calls)
        try:
            run_turn(client, session, user_input,
                     max_iterations=args.max_iterations, tracer=tracer)
        except Exception as exc:
            print(f"[agent error] {exc}", file=sys.stderr)
            session.append({"role": "assistant", "content": f"(turn failed: {exc})"})
            tracer.error(exc, phase="turn")

        # Per-turn context report.
        masked_placeholders = sum(
            1 for m in session if MASKING_INDICATOR in str(m.get("content") or "")
        )
        summaries = sum(
            1 for m in session if "Previous Conversation Summary" in str(m.get("content") or "")
        )
        est = sum(len(str(m.get("content") or "")) // 4 for m in session)
        print(f"    [session] {len(session)} msgs ~{est} tokens | "
              f"masked_placeholders={masked_placeholders} summaries={summaries}")

        # Per-turn token telemetry (real provider usage) + masking outlook.
        turn = _usage_sum(recorder.calls[calls_before:])
        tool_est = sum(len(str(m.get("content") or "")) // 4
                       for m in session if m.get("role") == "tool")
        mask_state = ("MASKING ARMED" if tool_est >= thres["mask_tool_at"] else "masking idle")
        over_state = ("OVER compress_at" if est >= thres["compress_at"] else "under compress_at")
        print(f"    [tokens] turn actual: prompt={turn['prompt']} completion={turn['completion']} "
              f"cached={turn['cached']} total={turn['total']} "
              f"({len(recorder.calls) - calls_before} llm calls)")
        print(f"    [tokens] tool_results ~{tool_est} / mask_tool_at {thres['mask_tool_at']} "
              f"-> {mask_state} | session ~{est} / compress_at {thres['compress_at']} "
              f"-> {over_state}")

    tracer.close()
    # PrototypeTracer writes JSONL; convert to a single JSON array file.
    trace_path = tracer.path
    if trace_path and os.path.exists(trace_path):
        json_path = os.path.splitext(trace_path)[0] + ".json"
        records = []
        with open(trace_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        os.remove(trace_path)
        trace_path = json_path
    print(f"\nfinal session: {len(session)} messages")
    if trace_path:
        print(f"trace: {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
