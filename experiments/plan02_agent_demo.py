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
    python experiments/plan02_agent_demo.py --model ... --turns "read this file" "summarize it"
    python experiments/plan02_agent_demo.py --model ... --no-context   # baseline
"""

from __future__ import annotations

import argparse
import asyncio
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Plan 02 context engine on the real prototype agent")
    parser.add_argument("--model", default="gemma4:cloud", help="provider/model or bare name")
    parser.add_argument("--provider", default=None, help="gemini | groq | openai | ollama")
    parser.add_argument("--context-length", type=int, default=None, help="override context window")
    parser.add_argument("--model-tier", default=None, choices=["small", "standard", "medium", "large"])
    parser.add_argument("--turns", nargs="*", default=None, help="non-interactive turns")
    parser.add_argument("--turns-file", default=None, help="file with one turn per line")
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
    session: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    client: Any = model
    ctx: Optional[FixedContextWindowManager] = None
    if args.context:
        ctx = build_context_manager(profile, model)
        shim = SessionShim(session)
        ctx._session_provider = lambda sid: shim  # persist into the same list
        client = ContextManagedClient(model, ctx, "prototype")

    tracer = PrototypeTracer(enabled=args.trace, provider=provider, model=model_name,
                             profile=profile.to_dict())
    tracer.session_start(SYSTEM_PROMPT, max_iterations=args.max_iterations)

    scripted: List[str] = list(args.turns) if args.turns else []
    if args.turns_file:
        with open(args.turns_file, "r", encoding="utf-8") as fh:
            scripted += [line.strip() for line in fh if line.strip()]

    index = 0
    while True:
        if index < len(scripted):
            user_input = scripted[index]
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

        if scripted and index >= len(scripted):
            break

    tracer.close()
    print(f"\nfinal session: {len(session)} messages")
    if tracer.path:
        print(f"trace: {tracer.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
