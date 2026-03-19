---
title: Agent (BaseClass)
description: The production-ready agent with tools, memory, and advanced control.
---

# Agent (Full-Featured)

Agent is the recommended production agent in Logicore. It supports tool execution, approval workflows, streaming, and optional memory.
Use it when BasicAgent is too limited and you need controlled multi-step workflows.

---

## How It Works + Use

- Initialize `Agent` with an LLM and optional tools.
- On each `chat()`, it can call tools, read results, then continue reasoning.
- Return value is the final assistant message as `str`.

Use it for production copilots, assistants with tools, and automation flows.

---

## Input Params

### Constructor

```python
Agent(
    llm: str | LLMProvider = "ollama",
    model: str = None,
    api_key: str = None,
    endpoint: str = None,
    system_message: str = None,
    role: str = "general",
    debug: bool = False,
    tools: list = [],
    max_iterations: int = 40,
    capabilities: Any = None,
    telemetry: bool = False,
    memory: bool = False,
    context_compression: bool = False,
    skills: list = None,
    workspace_root: str = None
)
```

This constructor configures the full execution engine for tool-enabled workflows.
`tools`, `memory`, and `max_iterations` are the most important production controls.
Use `debug` only in development to inspect reasoning and tool steps.

| Field | Required | Significance |
|---|---|---|
| `llm` | Conditionally (practical) | Chooses provider instance/backend. Core routing dependency for all model calls. |
| `model` | Conditionally (practical) | Ensures consistent model behavior and capability profile in production. |
| `api_key` | Conditionally | Mandatory for cloud providers; missing key causes auth errors. |
| Other constructor fields | No | Defaulted controls for memory, tools, limits, and diagnostics. |

### `chat()`

```python
await agent.chat(
    user_input: str | list,
    session_id: str = "default",
    callbacks: dict = None,
    stream: bool = False,
    streaming_funct: callable = None,
    generate_walkthrough: bool = False,
    **kwargs
)
```

This call runs the full agent loop: reason, optionally call tools, then synthesize the answer.
`callbacks` and `stream` are ideal for interactive UIs and observability.
Use `session_id` to isolate conversations by user, thread, or request context.

| Field | Required | Significance |
|---|---|---|
| `user_input` | Yes | Main instruction payload that drives reasoning and tool use. |
| `session_id` | No | Isolates or resumes per-user/per-thread conversation context. |
| `callbacks` | No | Enables observability hooks (tokens, tool lifecycle, approvals). |
| `stream` | No | Enables progressive output for chat-like UI responsiveness. |
| `streaming_funct` | No | Direct token callback shortcut; useful in terminal/CLI integrations. |
| `generate_walkthrough` | No | Returns brief execution narrative for auditability/demo use. |
| `**kwargs` | No | Passes provider-specific options (temperature, max tokens, etc.). |

---

## Response Params

```python
str  # final assistant message
```

The method returns a final text response after all tool iterations complete.
Intermediate tool activity is internal unless surfaced via callbacks.
For UX, stream tokens during execution and store the final string as the canonical answer.

---

## Examples

### 1) Basic Tool Use

```python
from logicore.agents.agent import Agent

def get_time() -> str:
    """Return current time."""
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")

agent = Agent(llm="ollama", tools=[get_time])
agent.set_auto_approve_all(True)

response = await agent.chat("What time is it?")
print(response)
```

This is the standard pattern for function-tool integration.
The tool is discoverable from its signature/docstring and can be called by the model during reasoning.
`set_auto_approve_all(True)` is useful for demos but should be restricted in production.

### 2) Streaming

```python
def on_token(token: str):
    print(token, end="", flush=True)

agent = Agent(llm="openai", model="gpt-4o-mini")
response = await agent.chat(
    "Explain event loops in Python",
    stream=True,
    callbacks={"on_token": on_token}
)
print("\nFinal:", response)
```

This example shows real-time token streaming for responsive interfaces.
You get immediate partial output while the model is still generating.
Use this for terminals, chat UIs, and long-form responses.

### 3) Approval Callback (Safer)

```python
async def approve_tool(session_id, tool_name, args):
    if tool_name in {"delete_file", "execute_command"}:
        return False
    return True

agent = Agent(llm="ollama", tools=[...], memory=True)
agent.set_callbacks(on_tool_approval=approve_tool)

response = await agent.chat("Clean temp files and summarize")
print(response)
```

This is the recommended safety pattern for production tool governance.
The callback evaluates each tool invocation and allows or blocks execution based on policy.
Use it to enforce least privilege and prevent risky tool operations.
