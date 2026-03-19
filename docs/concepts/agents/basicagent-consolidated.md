---
title: BasicAgent
description: The simplest entry point for rapid prototyping.
---

# BasicAgent

**BasicAgent** is the lightest-weight agent type for quick experiments and simple chatbots. It provides conversation capabilities without tools, memory persistence, or complex workflows—perfect for learning Logicore fundamentals or building basic Q&A chatbots.

---

## When to Use BasicAgent

- Learning Logicore fundamentals
- Simple Q&A chatbots (no tools needed)
- Rapid prototyping ideas
- Minimal dependencies required

---

## Quick Start

```python
from logicore.agents.agent_basic import BasicAgent
import asyncio

async def main():
    agent = BasicAgent(llm="ollama")
    response = await agent.chat("What is machine learning?")
    print(response['content'])

asyncio.run(main())
```

---

## How It Works

BasicAgent maintains a conversation context and remembers your session history. Each `chat()` call sends your message to the LLM and returns a structured response with the answer. It's stateless across sessions—create a new agent instance to start fresh.

**Supported LLM Providers:**
- `ollama` - Local execution (no API key needed)
- `openai` - OpenAI GPT models (requires `OPENAI_API_KEY`)
- `gemini` - Google Gemini (requires `GOOGLE_API_KEY`)
- `groq` - Groq fast inference (requires `GROQ_API_KEY`)
- `azure` - Azure OpenAI (requires `AZURE_OPENAI_KEY` and `AZURE_OPENAI_ENDPOINT`)

---

## Configuration Parameters

### Constructor Parameters

```python
agent = BasicAgent(
    llm: str = "ollama",                    # ✓ Required: LLM provider
    model: str = None,                      # Specific model (provider default if None)
    api_key: str = None,                    # API key (if cloud provider)
    system_message: str = None,             # Custom system instructions
    debug: bool = False,                    # Enable debug logging
    temperature: float = 0.7,               # LLM randomness (0-1)
    **kwargs
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | str | Required | Provider: `ollama`, `openai`, `gemini`, `groq`, `azure` |
| `model` | str | Provider default | Specific model name (e.g., `qwen2:7b`, `gpt-4`) |
| `api_key` | str | None | API key for cloud providers |
| `system_message` | str | None | Custom instructions for the agent |
| `debug` | bool | False | Print debug logs |
| `temperature` | float | 0.7 | Randomness: 0=deterministic, 1=creative |

---

## Chat Method: Input & Output

### Request Parameters

```python
response = await agent.chat(
    message: str,                           # ✓ Required: User prompt
    callbacks: Dict = None,                 # Optional: `{"on_token": callable}` for streaming
    stream: bool = False,                   # Optional: Enable streaming
    temperature: float = None,              # Optional: Override temperature
    max_tokens: int = None,                 # Optional: Max response length
    metadata: Dict = None                   # Optional: Additional context
)
```

### Response Schema

```python
{
    "role": "assistant",                    # Always "assistant"
    "content": str,                         # The actual response text
    "tool_calls": None,                     # Always None for BasicAgent
    "tokens_used": int,                     # Tokens in response
    "provider": str,                        # Provider used (e.g., "ollama")
    "model": str,                           # Model name (e.g., "qwen2:7b")
    "finish_reason": str,                   # "stop" or "max_tokens"
    "metadata": dict                        # Timestamps, session info
}
```

---

## Examples: Basic to Advanced

### Example 1: Simple Q&A

```python
from logicore.agents.agent_basic import BasicAgent
import asyncio

async def main():
    agent = BasicAgent(llm="ollama", model="qwen2:7b")
    
    response = await agent.chat("What is photosynthesis?")
    print(f"Answer: {response['content']}")
    print(f"Tokens used: {response['tokens_used']}")

asyncio.run(main())
```

**Output:**
```
Answer: Photosynthesis is the process by which plants convert light energy into chemical energy...
Tokens used: 87
```

---

### Example 2: Multi-Turn Conversation with Streaming

```python
agent = BasicAgent(
    llm="openai",
    model="gpt-4",
    api_key="sk-...",
    temperature=0.8  # More creative
)

def on_token(token):
    """Print each token as it arrives."""
    print(token, end="", flush=True)

# Turn 1: Introduce yourself
await agent.chat("My name is Alice and I work in AI research")

# Turn 2: Agent remembers
response = await agent.chat(
    "What's my name and what do I do?",
    callbacks={"on_token": on_token},
    stream=True  # Token-by-token output
)

print(f"\n\nFull response: {response['content']}")
```

**Output:**
```
Your name is Alice and you work in AI research...

Full response: Your name is Alice and you work in AI research. Based on our conversation, you're involved in AI research, which is an exciting field focused on...
```

---

### Example 3: Custom System Prompt & Specialized Behavior

```python
# Shakespeare expert agent
shakespeare_agent = BasicAgent(
    llm="ollama",
    model="mistral:7b",
    system_message="""You are an expert on Shakespeare's works.
    Answer all questions about Shakespeare in a theatrical, dramatic tone.
    Use quotes from the plays when possible.""",
    temperature=0.9  # More creative responses
)

response = await shakespeare_agent.chat(
    "Tell me about Hamlet's character"
)
print(response['content'])

# Content generator agent
creative_agent = BasicAgent(
    llm="openai",
    model="gpt-4",
    system_message="You are a creative writer. Generate engaging, unique content.",
    temperature=0.95  # Maximum creativity
)

response = await creative_agent.chat(
    "Write a haiku about artificial intelligence"
)
print(response['content'])
```

**Output:**
```
Hail, sweet Hamlet! Thou art a prince most troubled, caught between action and contemplation...

Silicon dreams wake,
Minds born of human design,
Teach us to think new.
```

---

## Limitations

BasicAgent has no support for:
- Tools or function calling
- Persistent memory across sessions (only session history)
- Tool approvals or safety workflows
- Skills loading
- Cron/scheduled tasks

**Need these features?** Upgrade to [Agent (Full)](./full-agent) for tools and memory, or [MCPAgent](../mcp/mcp-agent) for enterprise MCP tools.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **"Provider not found"** | Check provider name is lowercase: `ollama`, not `Ollama` |
| **API key errors** | Set environment: `export OPENAI_API_KEY="sk-..."` |
| **Ollama not responding** | Run `ollama serve` in a terminal, then pull model: `ollama pull qwen2:7b` |
| **Model not found** | Pull model first: `ollama pull mistral:7b` |
| **Streaming has no output** | Ensure `stream=True` AND `callbacks` dict with `on_token` is provided |

---

## Next Steps

- **[Agent (Full)](./full-agent)** — Add tools and persistent memory
- **[SmartAgent](./smart-agent)** — Project-aware with built-in tools
- **[MCPAgent](../mcp/mcp-agent)** — Enterprise MCP tools
- **[All Agents](./agents-overview)** — Compare all agent types
