import asyncio
from logicore.agents.agent import Agent

# Custom tool: simple echo

def echo(msg: str, **kwargs) -> str:
    """Return the provided message back to the user."""
    return f"Echo: {msg}"

async def main():
    # Create an agent that can use the echo tool
    agent = Agent(
        llm="ollama",  # change to your provider if needed
        role="Echo Agent",
        system_message="You are an assistant that echoes user messages using the echo tool.",
        tools=[echo],
        debug=True,
    )

    # One simple chat
    response = await agent.chat("Hello, agent!", stream=False)
    print("Agent response:", response["content"])

if __name__ == "__main__":
    asyncio.run(main())
