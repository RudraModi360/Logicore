---
title: BasicAgent
description: The simplest entry point for rapid prototyping.
---

# BasicAgent

BasicAgent is the lightest Logicore agent for quick chat-first use cases. It is designed for fast setup, minimal configuration, and simple interactions.
Use it when you want a clean starting point without complex orchestration.

---

## How It Works + Use

- Create `BasicAgent` with provider/model.
- Call `await agent.chat(message)`.
- Receive final assistant output as `str`.

Best for quick Q&A bots, prototypes, and lightweight assistants.

---

## Input Params

### Constructor

```python
BasicAgent(
    name: str = "Assistant",
    description: str = "A helpful AI assistant",
    provider: str = "ollama",
    model: str = None,
    api_key: str = None,
    tools: list = None,
    system_prompt: str = None,
    memory_enabled: bool = True,
    debug: bool = False,
    telemetry: bool = False,
    max_iterations: int = 20,
    skills: list = None,
    workspace_root: str = None,
    **kwargs
)
```

This constructor sets the core identity and runtime behavior of the agent.
In most cases, you only need `provider` and optionally `model`; the rest are control knobs for memory, logging, and integrations.
Use `system_prompt` to enforce role/style consistently across all turns.

| Field | Required | Significance |
|---|---|---|
| `provider` | Conditionally (practical) | Selects backend routing and model ecosystem. Wrong provider means failed initialization or unexpected behavior. |
| `api_key` | Conditionally | Required for cloud providers (`openai`, `azure`, etc.); without it authentication fails. |
| Other constructor fields | No | Have defaults; tune only when needed for behavior, safety, or observability. |

### `chat()`

```python
await agent.chat(
    message: str | list,
    session_id: str = "default",
    stream: bool = False,
    generate_walkthrough: bool = False,
    **kwargs
)
```

This is the main runtime call for every user interaction.
`session_id` keeps context across turns, while `stream=True` enables real-time token output.
Use `generate_walkthrough` when you want a short execution summary appended to the final answer.

| Field | Required | Significance |
|---|---|---|
| `message` | Yes | Primary user instruction; without it no chat action can be performed. |
| `session_id` | No | Controls multi-turn continuity; same ID preserves context across turns. |
| `stream` | No | Improves UX for long responses by emitting tokens incrementally. |
| `generate_walkthrough` | No | Appends a short execution summary useful for debugging/demo clarity. |
| `**kwargs` | No | Passes provider/runtime overrides for advanced control. |

---

## Response Params

```python
str  # final assistant message
```

BasicAgent returns a plain final response string.
No tool-call object is returned here, which keeps response handling simple in lightweight apps.
If you need richer structured traces, use `Agent` or `MCPAgent`.

---

## Examples

### 1) Basic Q&A

```python
from logicore.agents.agent_basic import BasicAgent
import asyncio

async def main():
    agent = BasicAgent(provider="ollama", model="qwen2:7b")
    response = await agent.chat("What is machine learning?")
    print(response)

asyncio.run(main())
```

This is the fastest start path for a single-turn assistant.
It shows the minimum viable setup for local inference with one prompt and one final output.
Use this pattern for smoke tests and onboarding examples.

### 2) Multi-turn Session

```python
agent = BasicAgent(provider="ollama")

await agent.chat("My favorite color is blue", session_id="u1")
response = await agent.chat("What is my favorite color?", session_id="u1")
print(response)
```

This example shows how conversation state is preserved by `session_id`.
Both turns belong to the same logical chat session, so the second prompt can reference earlier context.
Use session IDs per user/thread in multi-user apps.

### 3) Streaming + Custom Prompt

```python
def on_token(token: str):
    print(token, end="", flush=True)

agent = BasicAgent(
    provider="openai",
    system_prompt="You are a concise coding tutor."
)
agent.set_callbacks(on_token=on_token)

response = await agent.chat("Explain async/await in Python", stream=True)
print("\n---\nFinal:", response)
```

This pattern enables token-by-token UI rendering for better perceived latency.
Streaming improves UX for longer responses while still providing a final consolidated string at the end.
Use this in chat interfaces where users should see progress immediately.
