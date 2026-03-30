---
title: Agent (Full-Featured)
description: Production-ready agent with tools, memory, and advanced control.
---

# Agent (Full-Featured)

**Agent** is the standard, production-ready agent type. It supports custom tools, persistent memory, approval workflows, and real-time streaming—everything needed for production AI applications with advanced control.

---

## When to Use Agent

- Production applications requiring tool execution
- Building agents with persistent memory
- Custom approval and safety workflows
- Real-time streaming UIs
- Most real-world scenarios

---

## Quick Start

```python
from logicore.agents.agent import Agent
import asyncio

def check_weather(location: str) -> str:
    """Get the weather for a location."""
    return f"It's 72°F and sunny in {location}"

async def main():
    agent = Agent(
        llm="ollama",
        tools=[check_weather]
    )
    agent.set_auto_approve_all(True)
    
    response = await agent.chat("What's the weather in Seattle?")
    print(response['content'])

asyncio.run(main())
```

---

## How It Works

Agent combines an LLM with Python tools and a decision loop. When you ask a question, the agent decides if it needs tools. If yes, it calls them, gets results, and uses those results to answer. All conversations are stored in memory for context retrieval across sessions.

**Key capabilities:**
- **Tool execution** — Automatically calls Python functions with approval
- **Persistent memory** — Stores and retrieves context across sessions
- **Approval workflows** — Control which tools execute with custom callbacks
- **Streaming** — Real-time token output for responsive UIs
- **Multi-provider** — Works with ollama, OpenAI, Gemini, Groq, Azure

---

## Configuration Parameters

### Constructor Parameters

```python
agent = Agent(
    llm: str = "ollama",                    # ✓ Required: LLM provider
    model: str = None,                      # Specific model
    tools: List[Callable] = None,           # Python functions to use
    system_message: str = None,             # Custom instructions
    memory: bool = True,                    # Enable persistent memory
    memory_type: str = "default",           # "default", "short_term", "long_term"
    debug: bool = False,                    # Enable logging
    temperature: float = 0.7,               # LLM randomness
    max_iterations: int = 20,               # Max tool loop iterations
    **kwargs
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | str | Required | Provider: `ollama`, `openai`, `gemini`, `groq`, `azure` |
| `model` | str | Provider default | Model name (e.g., `qwen2:7b`, `gpt-4`) |
| `tools` | List | None | Python functions: `[func1, func2]` |
| `system_message` | str | None | Custom agent instructions |
| `memory` | bool | True | Enable cross-session memory |
| `memory_type` | str | "default" | "default", "short_term", "long_term" |
| `debug` | bool | False | Print execution details |
| `temperature` | float | 0.7 | Randomness (0-1) |
| `max_iterations` | int | 20 | Prevent infinite loops |

---

## Chat Method: Input & Output

### Request Parameters

```python
response = await agent.chat(
    message: str,                           # ✓ Required: Your prompt
    callbacks: Dict = None,                 # Optional: `{"on_token": fn, "on_tool_call": fn}`
    stream: bool = False,                   # Optional: Enable streaming
    approve_all: bool = False,              # Optional: Auto-approve all tools
    approval_filter: Callable = None,       # Optional: Decide per tool
    temperature: float = None,              # Optional: Override temperature
    max_tokens: int = None,                 # Optional: Max response length
    metadata: Dict = None                   # Optional: Additional context
)
```

### Response Schema

```python
{
    "role": "assistant",                    # Always "assistant"
    "content": str,                         # Final answer
    "tool_calls": List[Dict | None],        # Tools executed (if any)
    "tokens_used": int,                     # Total tokens
    "provider": str,                        # Provider used
    "model": str,                           # Model name
    "finish_reason": str,                   # "stop" or "max_tokens"
    "execution_steps": List[Dict],          # Tool execution history
    "memory_updated": bool,                 # Was memory updated?
    "metadata": dict                        # Timestamps, etc.
}
```

**Tool call object:**
```python
{
    "id": "call_123",
    "name": "check_weather",                # Tool name
    "arguments": {"location": "Seattle"},   # Input arguments
    "result": "72°F and sunny",             # Tool output
    "status": "success",                    # success/error
    "approved": True,                       # Was it approved?
    "execution_time_ms": 125                # How long it took
}
```

---

## Examples: Basic to Advanced

### Example 1: Simple Tool Execution

```python
def get_time() -> str:
    """Get the current time."""
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")

agent = Agent(
    llm="ollama",
    tools=[get_time],
    debug=True
)
agent.set_auto_approve_all(True)

response = await agent.chat("What time is it?")
print(response['content'])
print(f"Tools used: {len(response['tool_calls'])}")
```

**Output:**
```
The current time is 14:35:42.
Tools used: 1
```

---

### Example 2: Custom Approval Workflow

```python
def safe_tool(path: str) -> str:
    """Read a file (safe operation)."""
    with open(path) as f:
        return f.read()

def dangerous_tool(path: str) -> str:
    """Delete a file (dangerous operation)."""
    import os
    os.remove(path)
    return f"Deleted {path}"

# Control which tools execute
async def smart_approval(tool_name, args):
    """Auto-approve read, but require approval for delete."""
    if tool_name == "safe_tool":
        return True  # Always allow reads
    
    if tool_name == "dangerous_tool":
        user_input = input(f"Approve deleting {args['path']}? (y/n): ")
        return user_input.lower() == 'y'
    
    return True

agent = Agent(
    llm="openai",
    tools=[safe_tool, dangerous_tool]
)
agent.set_callbacks(on_tool_approval=smart_approval)

# This will approve automatically
response = await agent.chat("Read the config file")

# This will ask for confirmation
response = await agent.chat("Delete old logs")
```

---

### Example 3: Persistent Memory & Multi-Session

```python
# Session 1: Store knowledge
agent1 = Agent(llm="ollama", memory=True)

await agent1.chat("""
    Remember: Our API endpoint is at https://api.example.com/v1.
    Authentication uses JWT tokens. Default timeout is 30 seconds.
""")

# Session 2: Retrieve knowledge (different agent instance)
agent2 = Agent(llm="ollama", memory=True)

response = await agent2.chat(
    "What's our API endpoint and authentication method?"
)
print(response['content'])
# Agent retrieves: "https://api.example.com/v1 with JWT tokens..."
```

**Output:**
```
Based on our previous conversation, your API endpoint is at https://api.example.com/v1, 
and it uses JWT token authentication with a default timeout of 30 seconds.
```

---

### Example 4: Streaming with Real-Time Output

```python
def on_token(token):
    print(token, end="", flush=True)

response = await agent.chat(
    "Explain quantum computing in detail",
    callbacks={"on_token": on_token},
    stream=True
)

print("\n[Done]")
```

**Output:**
```
Quantum computing is a revolutionary computing paradigm that leverages quantum mechanical phenomena...
[Done]
```

---

## Key Features

| Feature | BasicAgent | Agent | MCPAgent |
|---------|-----------|-------|----------|
| Chat & Conversation | ✓ | ✓ | ✓ |
| Tool Execution | | ✓ | ✓ |
| Tool Approval | | ✓ | ✓ |
| Persistent Memory | | ✓ | ✓ |
| Custom Tools | | ✓ | ✓ |
| Streaming | ✓ | ✓ | ✓ |
| Enterprise MCP | | | ✓ |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **Tool not being called** | Add docstring to function (LLM needs description) |
| **"Max iterations exceeded"** | Reduce `max_iterations` or fix tool return values |
| **Memory not working** | Enable with `memory=True` and check write permissions |
| **Approval not triggering** | Set `auto_approve=False` so callbacks are invoked |
| **Tool takes too long** | Add timeout or convert to async |

---

## Next Steps

- **[SmartAgent](./smart-agent)** — Add project context and built-in tools
- **[MCPAgent](../mcp/mcp-agent)** — Enterprise MCP server tools
- **[BasicAgent](./basic-agent)** — Simpler alternative without tools
- **[Compare Agents](./agents-overview)** — Full feature comparison
