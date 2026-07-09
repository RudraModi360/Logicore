"""Simple interactive chatbot — using CustomProvider."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore import SmartAgent, BasicAgent, CopilotAgent, Agent

async def main():
    agent = SmartAgent(
        provider="ollama",
        model="gpt-oss:20b-cloud",
        debug=True,
        telemetry=False,
        max_iterations=60,
    )
    agent.set_auto_approve_all(True)
    print("Agent ready. Type 'quit' to exit.\n")
    while (msg := input("You: ").strip()) and msg != "quit":
        resp = await agent.chat(msg, stream=True, streaming_funct=lambda t: print(t, end="", flush=True))
        print()

asyncio.run(main())