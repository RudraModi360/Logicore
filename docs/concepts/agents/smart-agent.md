---
title: SmartAgent
description: Project-aware agent with built-in tools, dual modes, and intelligent learning.
---

# SmartAgent

SmartAgent extends Agent with built-in practical tools and project-awareness. It supports `solo` mode for general work and `project` mode for context-driven work.
Use it when you want strong defaults without manually wiring lots of tools.

---

## How It Works + Use

- Create `SmartAgent` with `mode="solo"` or `mode="project"`.
- It preloads useful tools (web/image search, notes, datetime, bash, cron, memory).
- `chat()` returns the final assistant output as `str`.

Use it for coding assistants, project workflows, and iterative team tasks.

---

## Input Params

### Constructor

```python
SmartAgent(
    llm: str | LLMProvider = "ollama",
    model: str = None,
    api_key: str = None,
    mode: str = "solo",          # "solo" | "project"
    project_id: str = None,
    debug: bool = False,
    telemetry: bool = False,
    memory: bool = False,
    max_iterations: int = 40,
    capabilities: Any = None,
    skills: list = None,
    workspace_root: str = None
)
```

This constructor enables dual-mode behavior plus built-in utility tools.
`mode` and `project_id` decide whether responses are generic or project-contextual.
Set `workspace_root` when you expect filesystem/bash tools to operate inside a known boundary.

| Field | Required | Significance |
|---|---|---|
| `llm` | Conditionally (practical) | Selects provider backend and determines runtime model interface. |
| `model` | Conditionally (practical) | Locks response characteristics and capability behavior for consistency. |
| `project_id` | Conditional | Required in `project` mode to bind context-aware reasoning to a project. |
| Other constructor fields | No | Optional controls for memory, diagnostics, and execution tuning. |

### `chat()`

```python
await agent.chat(
    user_input: str | list,
    session_id: str = "default",
    stream: bool = False,
    generate_walkthrough: bool = False,
    **kwargs
)
```

This is the main entry for both solo and project conversations.
The same method adapts behavior based on current mode and active project context.
Use `stream=True` when you need progressive output in interactive UIs.

| Field | Required | Significance |
|---|---|---|
| `user_input` | Yes | Core instruction text used for reasoning/tool selection. |
| `session_id` | No | Preserves ongoing context across turns within a thread. |
| `stream` | No | Enables incremental rendering in interactive developer UIs. |
| `generate_walkthrough` | No | Adds concise execution summary helpful in project workflows. |
| `**kwargs` | No | Allows provider/runtime overrides when needed. |

---

## Response Params

```python
str  # final assistant message
```

SmartAgent returns a final text answer after internal reasoning and tool usage.
Project context and captured learnings influence response quality, but output remains simple to consume.
This keeps integration straightforward in APIs and chat frontends.

---

## Examples

### 1) Solo Mode

```python
from logicore.agents.agent_smart import SmartAgent

agent = SmartAgent(llm="ollama", mode="solo")
response = await agent.chat("Find latest Python async best practices")
print(response)
```

This is the quickest way to use SmartAgent for open-ended tasks.
In solo mode, the agent is not constrained by project context and can explore broadly.
Use this for discovery, brainstorming, and ad-hoc technical queries.

### 2) Project Mode

```python
agent = SmartAgent(llm="ollama", mode="project")

agent.create_project(
    project_id="api-core",
    title="API Core",
    goal="Build stable authentication APIs",
    environment={"FRAMEWORK": "fastapi"},
    key_files=["src/", "tests/"]
)
agent.switch_to_project("api-core")

response = await agent.chat("Suggest auth flow and test plan")
print(response)
```

This pattern activates project-aware reasoning.
By setting goal, environment, and key files, the assistant gives recommendations aligned with your actual codebase intent.
Use this as the default mode for sustained product or engineering work.

### 3) Streaming + Mode Switch

```python
def on_token(token: str):
    print(token, end="", flush=True)

agent.switch_to_solo()
await agent.chat("Summarize current architecture", stream=True, callbacks={"on_token": on_token})

agent.switch_to_project("api-core")
response = await agent.chat("Refine only for API Core scope")
print("\nFinal:", response)
```

This example combines streaming UX with mode transitions.
It shows how the same agent can switch between broad analysis and project-constrained guidance.
Use this when one session needs both exploratory and delivery-focused phases.
