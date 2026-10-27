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
from logicore.runtime.context.token_estimator import TokenEstimator  # noqa: E402
from logicore.runtime.model_profile import ModelProfile  # noqa: E402
from experiments.context_engine import FixedContextWindowManager  # noqa: E402
from experiments.context_engine.distill import (  # noqa: E402
    ToolOutputDistiller,
    wrap_tool_impls,
)

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
    """Generalized system prompt — intelligence-driven, not task-forced.

    The agent decides, per turn, whether a tool call is needed:

    * Simple/factual queries the model can answer from its own knowledge
      → answer directly, no forced searches.
    * Queries needing current facts, local events, verification, or
      multi-city comparison → use web_search + read_file, search thoroughly
      with different keywords, don't stop after one result.
    * Coding tasks → research first, then write complete working code,
      verify it runs, and only then report done.

    This keeps the agent general: it works for coding, math, writing,
    research, or anything else — tools are available but not mandatory.
    """
    today = _dt.date.today().isoformat()
    return (
        SYSTEM_PROMPT
        + f"\nCurrent real-world date: {today}. Your training data may be stale; "
        + "when a question depends on current or local facts, verify with tools "
        + "before answering. When it doesn't, answer directly.\n"
        + "Operating principles:\n"
        + "A. The user's message is the spec. Do exactly what it asks — every part of it.\n"
        + "   If a request has multiple parts, complete ALL of them and confirm each one.\n"
        + "   Never substitute your own easier plan or silently drop a part.\n"
        + "B. Use tools only when they add value. For simple questions you can answer\n"
        + "   from your own knowledge, answer directly — do not force web searches.\n"
        + "   For questions needing current/local facts (news, events, prices, weather,\n"
        + "   specific cities), search thoroughly: multiple queries with different\n"
        + "   keywords, then read_file the full results. Never stop after one search.\n"
        + "   For coding tasks, research the approach first, then write complete\n"
        + "   working code, run it to verify, and only then report done.\n"
        + "C. Verify before claiming done. Confirm artifacts exist (read/list files),\n"
        + "   check numbers add up, run code to confirm it works, and report\n"
        + "   paths/results only after confirmation.\n"
        + "D. On failure, change approach — different keywords, different tool, different\n"
        + "   angle — never the identical retry. Report failures honestly.\n"
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
        self.provider_name = self.provider  # so TokenEstimator is provider-aware
        self.model = getattr(inner, "model", "default")

    async def chat(self, messages, tools=None, max_tokens: int = 2048):
        return self._inner.chat(messages, tools=tools, max_tokens=max_tokens)


class ContextManagedClient:
    """Wrap ModelClient.chat to run Plan 02 context management first.

    run_turn() calls this before every LLM request. manage() masks/compresses
    the session (writing back via the shim), then the real client sends the
    compacted transcript to the model.

    Also extracts and tracks key facts (user corrections, model references)
    so they are never lost to compression/truncation.
    """

    def __init__(self, inner: ModelClient, ctx: FixedContextWindowManager, session_id: str,
                 tracer: Optional[PrototypeTracer] = None):
        self._inner = inner
        self._ctx = ctx
        self._session_id = session_id
        self._tracer = tracer
        self.provider = getattr(inner, "provider", "")
        self.model = getattr(inner, "model", "")
        self.last_result: Optional[Any] = None
        # Key facts extracted from the conversation — preserved across
        # compression/truncation so the agent never forgets them.
        self.key_facts: List[str] = []

    def chat(self, messages, tools=None, max_tokens: int = 2048):
        # Extract key facts from the incoming messages before they get
        # compressed/truncated, so we can track them across turns.
        self._extract_key_facts(messages)

        result, managed = asyncio.run(self._ctx.manage(messages, self._session_id))
        self.last_result = result
        if self._tracer is not None:
            self._tracer.context_manage(result)
        if result.any_action_taken:
            flag = []
            if result.masked:
                flag.append(f"masked={result.masking_result.masked_count}")
            if result.compressed:
                flag.append("compressed")
            if result.truncated:
                flag.append("hard_enforced" if getattr(result, "hard_enforced", False) else "truncated")
            print(
                f"    [ctx] {result.original_tokens} -> {result.final_tokens} tokens "
                f"(saved {result.tokens_saved})  {', '.join(flag)}"
            )

        # Context pressure warning: if we're above 75% of the hard limit, warn.
        if result.hard_limit and result.original_tokens > result.hard_limit * 0.75:
            pct = result.original_tokens / result.hard_limit * 100
            print(
                f"    [ctx-pressure] {pct:.0f}% of hard limit — context is tight, "
                f"older messages may be compressed or dropped"
            )

        # Send the MANAGED (masked/compressed/truncated) transcript to the model,
        # not the raw input — otherwise context management has no effect.
        return self._inner.chat(managed, tools=tools, max_tokens=max_tokens)

    def _extract_key_facts(self, messages: List[Dict[str, Any]]) -> None:
        """Extract key facts from messages before they get compressed away."""
        import re
        for msg in messages:
            content = str(msg.get("content") or "")
            role = msg.get("role", "")
            if not content:
                continue
            if role == "user":
                for pat in (r"use\s+.+?\s+not\s+", r"i said", r"you got.*?wrong",
                            r"not\s+.+?\s+model", r"wrong.*?model"):
                    if re.search(pat, content, re.IGNORECASE):
                        fact = content.strip()[:200]
                        if fact not in self.key_facts:
                            self.key_facts.append(f"USER_CORRECTION: {fact}")
            if role == "tool":
                for pat in (r"MiniCPM[-o]?\s*\d*\.?\d*", r"Phi-?3\s*Vision",
                            r"Moondream2", r"Llava[-v]?\d*\.?\d*", r"Qwen2-VL-\d*\.?\d*"):
                    for m in re.finditer(pat, content, re.IGNORECASE):
                        fact = f"MODEL_REFERENCE: {m.group(1)}"
                        if fact not in self.key_facts:
                            self.key_facts.append(fact)


def build_context_manager(profile: ModelProfile, model_client: ModelClient) -> FixedContextWindowManager:
    config = RuntimeConfig.from_settings()
    config.apply_model_profile(profile)
    # A4: compression gets its OWN text-only connection (same provider/model).
    # The tool-calling client is never reused for summarization.
    compression_client = ModelClient(
        getattr(model_client, "provider", ""),
        getattr(model_client, "model", "default"),
    )
    return FixedContextWindowManager(
        config=config,
        llm_provider=AsyncLLM(compression_client),
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

    @property
    def last_result(self):
        return getattr(self._inner, "last_result", None)


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
    parser.add_argument("--distill", dest="distill", action="store_true", default=True,
                        help="distill bulky tool outputs at insertion (default)")
    parser.add_argument("--no-distill", dest="distill", action="store_false",
                        help="disable tool-output distillation (baseline)")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--turns", nargs="*", default=None, help="non-interactive turns (one per arg)")
    parser.add_argument("--turns-file", default=None, help="file with one turn per line")
    parser.add_argument("--task", default=None, help="predefined task: navratri-report | research-deep | multi-city | code-research | long-horizon")
    parser.add_argument("--pressure-test", dest="pressure_test", action="store_true", default=False,
                        help="shrink context window to 4096 so masking/compression trigger quickly for testing")
    parser.add_argument("--no-trace", dest="trace", action="store_false", default=True)
    args = parser.parse_args(argv)

    # Predefined long-horizon stress-test tasks. Each one combines web research
    # with coding, forcing the agent to: search multiple times, read full
    # results, then write and verify complete working code. This generates
    # enough tool output to actually exercise masking / compression / distillation.
    PREDEFINED_TASKS: Dict[str, List[str]] = {
        "navratri-report": [
            "Research Navratri festival incidents for yesterday (October 17, 2026) in Jaipur, Rajasthan. "
            "Search for news about accidents, caste issues, police actions, and community events during the 7th day of Navratri. "
            "Do multiple searches with different keywords. Read the full results. "
            "Come back with a structured report for my editor covering major and minor incidents.",
        ],
        "research-deep": [
            "Research the Navratri festival situation across multiple cities in Rajasthan (Jaipur, Udaipur, Jodhpur, Kota, Ajmer) for October 17, 2026. "
            "For each city, search for: accidents, crime incidents, police safety measures, community events, and temple gatherings. "
            "Do at least one web_search per city, then read_file the full results. "
            "Compile a comprehensive multi-city report comparing incidents across all cities.",
        ],
        "multi-city": [
            "Search for Navratri 7th day incidents in Rajasthan cities: Jaipur, Udaipur, Jodhpur, Kota, Bikaner, Ajmer, Alwar. "
            "Run web_search for each city separately with keywords like 'Navratri incident [city] October 17 2026'. "
            "Read every search result fully. Report which cities had incidents and what they were.",
        ],
        "code-research": [
            "Research the latest Python web scraping best practices for 2026: which libraries are most popular, "
            "how to handle rate limiting, and what user-agent strategies work. "
            "Do at least 3 different web searches with different keywords. "
            "Then write a complete Python script that scrapes a news site responsibly (respects robots.txt, "
            "adds delays, sets proper headers), save it to scraper.py, and run it to verify it works. "
            "Report what you found and confirm the script runs without errors.",
        ],
        "long-horizon": [
            "Research current state of AI agent frameworks in late 2026: search for comparisons of "
            "LangChain vs AutoGen vs crewAI vs new entrants, look at GitHub stars, recent releases, "
            "and key differentiators. Do at least 4 web searches across different queries. "
            "Then create a comprehensive comparison markdown file (agent_comparison.md) with a table "
            "of frameworks, their pros/cons, best use cases, and example code snippets for each. "
            "Read back the file to confirm it was written correctly, and list the directory to verify.",
        ],
    }

    # Pressure test: shrink the context window so masking/compression trigger
    # quickly, letting you verify the context engine under real load.
    if args.pressure_test:
        if args.context_length is not None:
            print(f"[pressure-test] overriding --context-length {args.context_length} -> 4096", file=sys.stderr)
        model_config["context_length"] = 4096

    # Resolve turns from --task, --turns, or --turns-file.
    scripted_turns: List[str] = []
    if args.task:
        if args.task not in PREDEFINED_TASKS:
            print(f"Unknown task '{args.task}'. Available: {', '.join(PREDEFINED_TASKS)}", file=sys.stderr)
            return 2
        scripted_turns = list(PREDEFINED_TASKS[args.task])
    if args.turns:
        scripted_turns += list(args.turns)
    if args.turns_file:
        with open(args.turns_file, "r", encoding="utf-8") as fh:
            scripted_turns += [line.strip() for line in fh if line.strip()]

    provider, model_name = resolve_provider_model(args.model, args.provider)
    model_config: Dict[str, Any] = {}
    if args.context_length is not None and not args.pressure_test:
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

    # A5: one provider-aware estimator is the single token authority for the
    # tracer and the context engine (both read from it; no local char/4 math).
    estimator = TokenEstimator(provider=provider, model=model_name)

    # Group B: distill bulky tool outputs at insertion (prevention). Threshold
    # scales with the window (window // 10, floor 500) so small windows distill
    # aggressively and large ones only the truly bulky payloads — same scaling
    # philosophy as A3's min_prunable floor.
    distiller: Optional[ToolOutputDistiller] = None
    if args.distill:
        distill_at = max(500, profile.context_length // 10)
        distiller = ToolOutputDistiller(estimator, threshold_tokens=distill_at)
        wrapped = wrap_tool_impls(_proto.TOOL_IMPLS, distiller, session_id="prototype")
        print(f"[distill] ON | threshold={distill_at} tokens "
              f"(= window {profile.context_length} // 10) | wrapped {wrapped} tools")
    else:
        print("[distill] OFF | baseline (full tool outputs enter the session)")

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
                             profile=profile.to_dict(), estimator=estimator,
                             trace_dir=os.path.join(_REPO_ROOT, "debug", "traces", "plan02"))
    if isinstance(client, UsageRecorder) and isinstance(client._inner, ContextManagedClient):
        client._inner._tracer = tracer  # emit context_manage per LLM request
    tracer.session_start(demo_prompt, max_iterations=args.max_iterations)

    print("Type your turn live at the prompt below. exit / quit / q (or Ctrl+C) ends the session.")

    # Counters for end-of-session telemetry report.
    total_ctx_actions = 0
    total_masked = 0
    total_compressed = 0
    total_truncated = 0
    total_hard_enforced = 0
    total_tokens_saved = 0
    total_llm_calls = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cached_tokens = 0

    index = 0
    while True:
        if index < len(scripted_turns):
            user_input = scripted_turns[index]
            print(f"\n[user] {user_input}")
        else:
            try:
                user_input = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue
        index += 1

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

        # Per-turn context report (authoritative estimator, not char/4).
        masked_placeholders = sum(
            1 for m in session if MASKING_INDICATOR in str(m.get("content") or "")
        )
        summaries = sum(
            1 for m in session if "Previous Conversation Summary" in str(m.get("content") or "")
        )
        est = estimator.count_messages_tokens(session)
        print(f"    [session] {len(session)} msgs ~{est} tokens | "
              f"masked_placeholders={masked_placeholders} summaries={summaries}")
        if distiller is not None:
            s = distiller.stats
            print(f"    [distill] checked={s['checked']} distilled={s['distilled']} "
                  f"tokens_saved={s['tokens_saved']}")
        if isinstance(client, UsageRecorder) and isinstance(client._inner, ContextManagedClient):
            kf = client._inner.key_facts
            if kf:
                print(f"    [key-facts] {len(kf)} tracked: " + " | ".join(kf[:3]))

        # Per-turn token telemetry (real provider usage) + masking outlook.
        turn = _usage_sum(recorder.calls[calls_before:])
        tool_msgs = [m for m in session if m.get("role") == "tool"]
        tool_est = sum(estimator.count_message_tokens(m) for m in tool_msgs)
        mask_state = ("MASKING ARMED" if tool_est >= thres["mask_tool_at"] else "masking idle")
        over_state = ("OVER compress_at" if est >= thres["compress_at"] else "under compress_at")
        print(f"    [tokens] turn actual: prompt={turn['prompt']} completion={turn['completion']} "
              f"cached={turn['cached']} total={turn['total']} "
              f"({len(recorder.calls) - calls_before} llm calls)")
        print(f"    [tokens] tool_results ~{tool_est} / mask_tool_at {thres['mask_tool_at']} "
              f"-> {mask_state} | session ~{est} / compress_at {thres['compress_at']} "
              f"-> {over_state}")

        # Accumulate for end-of-session report.
        last_ctx = client.last_result if hasattr(client, "last_result") else None
        if last_ctx is not None:
            total_ctx_actions += 1 if last_ctx.any_action_taken else 0
            total_masked += last_ctx.masking_result.masked_count if last_ctx.masking_result else 0
            total_compressed += 1 if last_ctx.compressed else 0
            total_truncated += 1 if last_ctx.truncated else 0
            total_hard_enforced += 1 if getattr(last_ctx, "hard_enforced", False) else 0
            total_tokens_saved += last_ctx.tokens_saved
        total_llm_calls += len(recorder.calls) - calls_before
        total_prompt_tokens += turn["prompt"]
        total_completion_tokens += turn["completion"]
        total_cached_tokens += turn["cached"]

        if scripted_turns and index >= len(scripted_turns):
            break

    # ---- End-of-session telemetry report ----
    print("\n" + "=" * 60)
    print("SESSION TELEMETRY REPORT")
    print("=" * 60)
    print(f"Model:           {provider}/{model_name}  (tier={profile.tier}, window={profile.context_length})")
    print(f"Context mgmt:    {'ON' if args.context else 'OFF'}")
    print(f"Distillation:    {'ON' if args.distill else 'OFF'}")
    print(f"Turns:           {tracer.counters['turns']}")
    print(f"LLM calls:       {total_llm_calls}")
    print(f"Tool calls:      {tracer.counters['tool_calls']}  (errors: {tracer.counters['tool_errors']})")
    print()
    print("Context management actions:")
    print(f"  Actions taken:   {total_ctx_actions}")
    print(f"  Maskings:        {total_masked} outputs masked")
    print(f"  Compressions:    {total_compressed}")
    print(f"  Truncations:     {total_truncated}  (hard_enforced: {total_hard_enforced})")
    print(f"  Tokens saved:    {total_tokens_saved}")
    print()
    print("Token telemetry (real provider usage):")
    print(f"  Prompt tokens:   {total_prompt_tokens}")
    print(f"  Completion:      {total_completion_tokens}")
    print(f"  Cached:          {total_cached_tokens}")
    print(f"  Total:           {total_prompt_tokens + total_completion_tokens}")
    print()
    print("Session state:")
    print(f"  Messages:        {len(session)}")
    est = estimator.count_messages_tokens(session)
    print(f"  Estimated tokens: ~{est}")
    masked_placeholders = sum(
        1 for m in session if MASKING_INDICATOR in str(m.get("content") or "")
    )
    summaries = sum(
        1 for m in session if "Previous Conversation Summary" in str(m.get("content") or "")
    )
    print(f"  Masked placeholders: {masked_placeholders}")
    print(f"  Summaries:           {summaries}")
    if isinstance(client, UsageRecorder) and isinstance(client._inner, ContextManagedClient):
        kf = client._inner.key_facts
        print(f"  Key facts tracked:   {len(kf)}")
        for f in kf[:5]:
            print(f"    - {f}")
    if distiller is not None:
        s = distiller.stats
        print(f"  Distiller:         checked={s['checked']} distilled={s['distilled']} "
              f"tokens_saved={s['tokens_saved']}")
    print("=" * 60)

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
