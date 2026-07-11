"""
Final demo: Agent + persisted session management + persistent memory.

What this shows
--------------
- Session management: chat history is saved to the 3-tier storage under
  ~/.logicore (DB + snapshot). Re-running the script RESUMES the same session
  automatically (memories of past turns are restored).
- Persistent memory: the MemoryManager learns from each conversation and
  recalls relevant past context on future turns (stored under ~/.logicore/memory).

How to run (defaults + your provider/model only)
------------------------------------------------
    python scripts/smart_agent_demo.py --provider groq --model llama-3.3-70b-versatile
    python scripts/smart_agent_demo.py --provider ollama --model qwen3:0.6b

API keys are read automatically from the environment (.env / shell), so you
only need to pass the provider name and model name. Type `quit` to exit.

NOTE on "SmartAgent":
    SmartAgent is a subclass of Agent but does not forward the `storage=`
    parameter, so it cannot persist sessions. This demo uses the base Agent
    (the identical engine) so both session persistence AND memory work.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from examples.native_chatbot import SESSION_ID
from logicore import Agent
from logicore.storage import create_storage
from logicore.memory import MemoryManager

# SESSION_ID = "smart-agent-demo"


async def main(provider: str, model: str):
    # (1) Storage-backed persistence (sessions live under ~/.logicore)
    storage = create_storage()
    agent = Agent(provider=provider, model=model or None, storage=storage, debug=True)

    # (2) Resume an existing persisted session if present
    SESSION_ID=agent.create_session()
    print(f"[new] started fresh session '{SESSION_ID}'")

    # (3) Attach persistent memory (standalone subsystem, driven per turn)
    memory = MemoryManager(
        llm_provider=provider,
        llm_model=model or None,
        debug=True,
    )
    await memory.start()

    try:
        while True:
            try:
                msg = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not msg or msg.lower() in ("quit", "exit"):
                break

            # Recall relevant past memories, then run the turn
            messages = [{"role": "user", "content": msg}]
            messages = await memory.inject_context(
                messages, user_input=msg, use_llm_selection=True
            )

            resp = await agent.chat(
                messages,  # pass enriched messages so orchestrator adds memory context
                session_id=SESSION_ID,  # chat() auto-persists the session
                stream=True,
                streaming_funct=lambda t: print(t, end="", flush=True),
            )
            print()

            # Persist what we learned from this turn
            await memory.submit_for_extraction(
                messages + [{"role": "assistant", "content": resp}],
                session_id=SESSION_ID,
            )
            if memory.worker:
                await memory.worker._extraction_queue.join()
    finally:
        await memory.stop()
        storage.shutdown()
        print("\n[done] session + memory state saved under ~/.logicore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SmartAgent demo with sessions + persistent memory")
    parser.add_argument("--provider", default=os.environ.get("PROVIDER", "ollama"))
    parser.add_argument("--model", default=os.environ.get("MODEL", "gpt-oss:20b-cloud"))
    args = parser.parse_args()
    asyncio.run(main(args.provider, args.model))
