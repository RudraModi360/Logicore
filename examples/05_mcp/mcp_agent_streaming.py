"""
MCP Agent — streaming variant with external MCP servers.

Shows:
  - MCPAgent + stream_run() -> AgentRunResult async iterable
  - StreamEvent consumption for MCP tool calls
  - How MCP tools appear in the streaming event stream
  - Deferred tool discovery + streaming combination

Run:
    python examples/05_mcp/mcp_agent_streaming.py
"""

from __future__ import annotations
import argparse, os, asyncio
from logicore.agent.variants.mcp import MCPAgent
from logicore.stream.events import StreamEventType
from logicore.utils.colors import colored, error, success, tool_call, tool_result, info, BLUE


async def run(provider: str, model: str):
    mcp_config_path = os.path.join(os.path.dirname(__file__), "..", "..", "mcp.json")
    if not os.path.exists(mcp_config_path):
        mcp_config_path = "mcp.json"

    agent = MCPAgent(
        provider=provider,
        model=model,
        mcp_config_path=mcp_config_path if os.path.exists(mcp_config_path) else None,
        deferred_tools=True,
        debug=True,
        tool_threshold=100,
    )

    if os.path.exists(mcp_config_path):
        await agent.init_mcp_servers()

    print(f"Agent: {colored('MCPAgent', BLUE)} (streaming)  |  Deferred: True")
    print("Type 'quit' to exit\n")

    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not msg or msg.lower() in ("quit", "exit"):
            break

        # stream_run returns an AgentRunResult (both async iterable + awaitable)
        run_result = await agent.stream_run(msg)

        async for ev in run_result.stream_events():
            if ev.type == StreamEventType.TOKEN:
                print(ev.data.get("delta", ""), end="", flush=True)
            elif ev.type == StreamEventType.TOOL_CALL_START:
                name = ev.data.get("name", "?")
                print(f"\n{tool_call(f'[mcp] {name}')}", end="", flush=True)
            elif ev.type == StreamEventType.TOOL_CALL_END:
                status = "ok" if ev.data.get("success") else "FAIL"
                print(f" {tool_result(ev.data.get('success'), status)}", flush=True)
            elif ev.type == StreamEventType.TOOL_OUTPUT:
                stream = ev.data.get("stream", "stdout")
                line = ev.data.get("line", "")
                if stream == "stderr":
                    print(f"[stderr] {line}", flush=True)
                else:
                    print(line, flush=True)
            elif ev.type == StreamEventType.ERROR:
                print(f"\n{error('[error]')} {ev.data.get('message')}", flush=True)

        # Await the final result (optional, already consumed via events)
        final = await run_result
        print(f"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="gemma4:cloud")
    args = parser.parse_args()
    asyncio.run(run(args.provider, args.model))


if __name__ == "__main__":
    main()
