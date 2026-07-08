"""Simple interactive chatbot — using CustomProvider."""
import asyncio
from logicore import SmartAgent, BasicAgent,CopilotAgent

async def main():
    agent = BasicAgent(provider="gemini", model="gemini-3.1-flash-lite", debug=True, telemetry=True, max_iterations=60)
    # agent=CopilotAgent()
    # Auto-approve all tools (no manual approval prompts)
    # agent.set_auto_approve_all(True)
    print("SmartAgent ready. Type 'quit' to exit.\n")
    while (msg := input("You: ").strip()) and msg != "quit":
        resp = await agent.chat(msg, stream=True, streaming_funct=lambda t: print(t, end="", flush=True))
        print()

asyncio.run(main())

