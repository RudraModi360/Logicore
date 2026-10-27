"""Agent summarization probe — short local context, raw dump of every compression prompt.

Runs an interactive Agent on a small local Ollama model with a deliberately short
context window so the summarization (compression) stage fires after a few turns.
Every input handed to the summarization LLM is written byte-for-byte to a .txt
file — no headers, no JSON, no reformatting — so it can be replayed or inspected
exactly as the model received it.

The run stops right after the write: the summarization LLM call is NOT made, the
process exits so the captured file can be worked on.

    python scripts/agent_compress_probe.py
    python scripts/agent_compress_probe.py --context 2048 --compress-ratio 0.5 --preserve 4
    python scripts/agent_compress_probe.py --dump-dir debug/summary_inputs --no-tools

Dumps land in scripts/compression_inputs/compress_input_<timestamp>_<n>.txt
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from logicore import Agent
from logicore.runtime.context.compression import CompressionService
from logicore.stream.events import StreamEventType
from logicore.utils.colors import (
    BLUE,
    DIM,
    GRAY,
    RESET,
    error,
    info,
    success,
    tool_call,
    tool_result,
    warning,
)

DEFAULT_DUMP_DIR = Path(__file__).resolve().parent / "compression_inputs"
DUMP_FILES: List[Path] = []

DEEP_RESEARCH_SYSTEM_PROMPT = """You are a deep research assistant.

Method:
1. Restate the question and split it into 2-4 sub-questions.
2. Answer each sub-question from what you know; mark anything unverified.
3. Cross-check claims against each other and drop contradictions.

Reply as a compact report:
- Findings (bulleted, one fact each)
- Evidence (the basis for each finding)
- Caveats (what is missing, uncertain or out of scope)

Be concise and factual. Never invent sources, numbers or quotes."""


def raw_input_bytes(messages: List[Dict[str, Any]]) -> bytes:
    chunks = []
    for message in messages or []:
        content = message.get("content", "")
        chunks.append(content if isinstance(content, str) else str(content))
    return "\n".join(chunks).encode("utf-8")


class SummarizationInputDumper:
    """Proxy around the compression LLM that writes its input to disk first."""

    def __init__(self, inner: Any, dump_dir: Path):
        self._inner = inner
        self._dump_dir = dump_dir

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def chat(self, messages: List[Dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        payload = raw_input_bytes(messages)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = self._dump_dir / f"compress_input_{stamp}_{len(DUMP_FILES) + 1:03d}.txt"
        path.write_bytes(payload)
        DUMP_FILES.append(path)
        print(f"{info('[dump]')} summarization input -> {path} ({len(payload)} bytes)")
        print(f"{warning('[dump]')} input captured, session crashed before the LLM call")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


_HOOK_INSTALLED = False


def install_dump_hook(dump_dir: Path) -> None:
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    _HOOK_INSTALLED = True
    original = CompressionService.compress

    async def compress_with_dump(
        self, messages: List[Dict[str, Any]], session_id: str = "default"
    ):
        inner = self.llm
        if inner is None or isinstance(inner, SummarizationInputDumper):
            return await original(self, messages, session_id)
        self.llm = SummarizationInputDumper(inner, dump_dir)
        try:
            return await original(self, messages, session_id)
        finally:
            self.llm = inner

    CompressionService.compress = compress_with_dump


def make_stream_handler():
    in_thinking = [False]

    def on_event(ev):
        if ev.type == StreamEventType.TOKEN:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.REASONING:
            if not in_thinking[0]:
                print(GRAY, end="", flush=True)
                in_thinking[0] = True
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_START:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            name = ev.data.get("name", "?")
            args = ev.data.get("args", {})
            print(f"\n{tool_call(name)}({str(args)[:100]})", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_END:
            preview = str(ev.data.get("preview", ""))[:120]
            print(f" {tool_result(ev.data.get('success'), preview)}", flush=True)
        elif ev.type == StreamEventType.TOOL_OUTPUT:
            if ev.data.get("stream") == "stderr":
                print(f"{DIM}[stderr]{RESET} {ev.data.get('line', '')}", flush=True)
            else:
                print(ev.data.get("line", ""), flush=True)
        elif ev.type == StreamEventType.ERROR:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False
            print(f"\n{error('[error]')} {ev.data.get('message')}", flush=True)
        elif ev.type == StreamEventType.DONE:
            if in_thinking[0]:
                print(RESET, end="", flush=True)
                in_thinking[0] = False

    return on_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Short-context agent probe that dumps summarization prompts as-is."
    )
    parser.add_argument("--model", default="qwen3.5:0.8b", help="Ollama model tag")
    parser.add_argument("--context", type=int, default=16384, help="context_length override")
    parser.add_argument(
        "--compress-ratio",
        type=float,
        default=0.6,
        help="fraction of the window at which summarization fires",
    )
    parser.add_argument(
        "--preserve",
        type=int,
        default=6,
        help="recent messages kept verbatim (compression needs len(messages) > this + 2)",
    )
    parser.add_argument(
        "--preserve-ratio",
        type=float,
        default=0.85,
        help="truncation floor as a fraction of the threshold; keep >= 0.7 so truncation cannot starve summarization",
    )
    parser.add_argument("--dump-dir", default=str(DEFAULT_DUMP_DIR))
    parser.add_argument("--no-tools", action="store_true", help="disable agent tools")
    parser.add_argument("--session", default="compress-probe")
    return parser.parse_args()


def print_probe_status(agent: Agent, session_id: str) -> None:
    status = agent.context_engine.get_budget_status()
    session = agent.get_session(session_id)
    tokens = agent.context_engine.token_estimator.count_messages_tokens(session.messages)
    threshold = status.get("compression_threshold", 0)
    print(
        f"{DIM}[probe] context {tokens}/{threshold} tokens | "
        f"messages {len(session.messages)} | "
        f"compress={'YES' if tokens >= threshold else 'no'} | "
        f"dumps {len(DUMP_FILES)}{RESET}"
    )


async def main() -> None:
    args = parse_args()
    dump_dir = Path(args.dump_dir).expanduser().resolve()
    dump_dir.mkdir(parents=True, exist_ok=True)
    install_dump_hook(dump_dir)

    agent = Agent(
        provider="ollama",
        model=args.model,
        system_prompt=DEEP_RESEARCH_SYSTEM_PROMPT,
        debug=False,
        telemetry=False,
        max_iterations=20,
        model_config={
            "context_length": args.context,
            "model_tier": "standard",
            "compression_ratio": args.compress_ratio,
        },
    )
    if args.no_tools:
        agent.disable_tools("summarization probe")

    context_config = agent.runtime_config.context
    context_config.preserve_recent_count = args.preserve
    context_config.preserve_recent_ratio = args.preserve_ratio

    status = agent.context_engine.get_budget_status()
    threshold = status.get("compression_threshold", 0)
    truncation_floor = max(int(threshold * context_config.preserve_recent_ratio), 1000)
    truncate_at = int(truncation_floor * 1.5)
    hard_limit = int(
        (status.get("context_window", args.context) - status.get("reserved_for_response", 0))
        * (1 - context_config.hard_limit_margin)
    )

    print(f"{info('[probe]')} model        : {BLUE}{agent.model_name}{RESET}")
    print(
        f"{info('[probe]')} system       : "
        f"{agent.context_engine.token_estimator.count_tokens(agent.default_system_message)} tokens "
        f"(deep research prompt)"
    )
    print(f"{info('[probe]')} window       : {args.context} tokens")
    print(f"{info('[probe]')} compress at  : {threshold} tokens")
    print(f"{info('[probe]')} truncate at  : {truncate_at} tokens (floor {truncation_floor})")
    print(f"{info('[probe]')} hard limit   : {hard_limit} tokens")
    print(f"{info('[probe]')} preserve     : {args.preserve} recent messages")
    print(f"{info('[probe]')} dump dir     : {dump_dir}")
    if truncate_at <= threshold or hard_limit < threshold:
        print(
            warning("[probe]")
            + " summarization window is blocked — raise --preserve-ratio or lower --compress-ratio"
        )
    print(f"{info('[probe]')} summarization LLM input is written raw; 'quit' to exit.\n")

    on_event = make_stream_handler()
    dumps_seen = 0

    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg in ("quit", "exit"):
            break

        run = await agent.stream_run(
            msg, session_id=args.session, generate_walkthrough=False
        )
        try:
            async for ev in run.stream_events():
                on_event(ev)
        except asyncio.CancelledError:
            pass
        await run
        print()

        print_probe_status(agent, args.session)
        if len(DUMP_FILES) > dumps_seen:
            for path in DUMP_FILES[dumps_seen:]:
                print(f"{success('[probe]')} captured -> {path}")
            dumps_seen = len(DUMP_FILES)
        print()

    if DUMP_FILES:
        print(f"{success('[probe]')} {len(DUMP_FILES)} summarization input(s) captured:")
        for path in DUMP_FILES:
            print(f"  {path}")
    else:
        print(
            warning("[probe]")
            + " no summarization fired — lower --context/--compress-ratio or send longer messages"
        )


if __name__ == "__main__":
    asyncio.run(main())
