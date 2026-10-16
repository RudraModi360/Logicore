"""Simple interactive chatbot — using SmartAgent with live streaming."""

import asyncio
import json
import os


from logicore import SmartAgent,MCPAgent , BasicAgent , Agent
from logicore.stream.events import StreamEventType
from logicore.utils.colors import (
    colored,
    error,
    tool_call,
    tool_result,
    DIM,
    RESET,
    GRAY,
    BLUE,
)


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
            print(f"\n{tool_call(name)}({json.dumps(args)[:100]})", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_END:
            preview = str(ev.data.get("preview", ""))[:120]
            print(f" {tool_result(ev.data.get('success'), preview)}", flush=True)
        elif ev.type == StreamEventType.TOOL_OUTPUT:
            stream = ev.data.get("stream", "stdout")
            line = ev.data.get("line", "")
            if stream == "stderr":
                print(f"{DIM}[stderr]{RESET} {line}", flush=True)
            else:
                print(line, flush=True)
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


async def main():
    agent = Agent(
        provider="ollama",
        model="gemma4:cloud",
        debug=False,
        telemetry=False,
        max_iterations=60,
        # tools=[],
        model_config={"context_length": 32768, "model_tier": "large"},
    )
    
    print(f"Using model: {BLUE}{agent.model_name}{RESET}\n")
    p = agent.model_profile
    print("context_length:", p.context_length)
    print("tier:", p.tier)
    print("masking_ratio:", p.masking_ratio, "-> protect", p.protection_threshold_tokens(), "tokens")
    print("compression_ratio:", p.compression_ratio, "-> compress at", p.compression_threshold_tokens(), "tokens")
    on_event = make_stream_handler()
    print("Agent ready. Type 'quit' to exit.\n")
    while (msg := input("You: ").strip()) and msg != "quit":
        run = await agent.stream_run(msg)
        try:
            async for ev in run.stream_events():
                on_event(ev)
        except asyncio.CancelledError:
            pass
        await run
        print()


asyncio.run(main())
