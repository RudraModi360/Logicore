"""SmartAgent with persistent memory across sessions."""
import asyncio
from logicore import SmartAgent,BasicAgent
from logicore.memory import MemoryManager

async def main():
    # Initialize memory manager with LLM for background extraction.
    # llm_provider may be a provider instance OR a name string; llm_model
    # selects a (optionally different/cheaper) model for memory tasks.
    memory = MemoryManager(
        memory_dir="./agent_memory",
        llm_provider="ollama",
        llm_model="gemma3:4b-cloud",  # model used for extraction/retrieval
        throttle_interval=1.0,
        debug=True,
    )
    await memory.start()

    # Create agent with memory integration
    agent = BasicAgent(provider="ollama", model="gemma3:4b-cloud", debug=True)

    print("SmartAgent with memory ready. Type 'quit' to exit.\n")

    while (msg := input("You: ").strip()) and msg != "quit":
        # Inject relevant memories into context before chat
        # (use_llm_selection=True engages LLM re-ranking for recall/question intent)
        messages = [{"role": "user", "content": msg}]
        messages = await memory.inject_context(messages, user_input=msg, use_llm_selection=True)

        # Chat with memory context
        resp = await agent.chat(
            msg,
            stream=True,
            streaming_funct=lambda t: print(t, end="", flush=True),
        )
        print()

        # Submit conversation for background memory extraction and wait for it
        # to finish so the local LLM isn't hit concurrently by the next turn.
        await memory.submit_for_extraction(
            messages + [{"role": "assistant", "content": resp}],
            session_id="default",
        )
        if memory.worker:
            await memory.worker._extraction_queue.join()
        print("[Memory] Extraction complete.")

    # Wait for pending extractions before exit
    print("[Memory] Flushing pending extractions...")
    if memory.worker:
        await memory.worker._extraction_queue.join()
    await memory.stop()
    print("[Memory] Done.")

asyncio.run(main())
