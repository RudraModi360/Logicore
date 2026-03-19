---
title: MCPAgent
description: Enterprise-scale agents with custom MCP tools and advanced governance.
---

# MCPAgent

MCPAgent is the enterprise-focused Logicore agent for large tool ecosystems. It adds MCP server integration, session lifecycle utilities, and optional deferred tool loading.
Use it when you need many tools, stronger governance, or custom MCP infrastructure.

---

## How It Works + Use

- Initialize `MCPAgent` with provider/model and optional MCP config.
- It can load default + MCP tools and execute them during `chat()`.
- `chat()` returns final assistant output as `str` (or `None` if no final response).

Use it for enterprise assistants, regulated workflows, and large multi-tool environments.

---

## Input Params

### Constructor

```python
MCPAgent(
    provider: str | LLMProvider = "ollama",
    model: str = None,
    api_key: str = None,
    endpoint: str = None,
    system_message: str = "You are a helpful AI assistant with access to various tools.",
    debug: bool = False,
    telemetry: bool = False,
    memory: bool = False,
    max_iterations: int = 40,
    session_timeout: int = 3600,
    mcp_config_path: str = None,
    mcp_config: dict = None,
    deferred_tools: bool = False,
    tool_threshold: int = 15,
    skills: list = None,
    workspace_root: str = None
)
```

This constructor configures enterprise-grade behavior for large tool ecosystems.
`deferred_tools` and `tool_threshold` help control prompt/tool payload size when many tools exist.
Use `session_timeout` and telemetry settings to improve operational control in long-running deployments.

| Field | Required | Significance |
|---|---|---|
| `provider` | Conditionally (practical) | Determines the LLM backend used across MCP workflows. |
| `model` | Conditionally (practical) | Aligns capability/performance profile with enterprise use case. |
| `api_key` | Conditionally | Required for cloud backends; missing auth blocks requests. |
| Other constructor fields | No | Governance, session, and scaling controls with safe defaults. |

### `chat()`

```python
await agent.chat(
    user_input: str,
    session_id: str = "default",
    create_if_missing: bool = True,
    stream: bool = False,
    streaming_funct: callable = None,
    generate_walkthrough: bool = False,
    **kwargs
)
```

This call executes within a named MCP session and can auto-create missing sessions.
It supports both standard and streamed interactions while retaining the same execution contract.
Use `generate_walkthrough` if you need concise run summaries for audits/debugging.

| Field | Required | Significance |
|---|---|---|
| `user_input` | Yes | Primary instruction processed in MCP-enabled execution loop. |
| `session_id` | No | Binds request to a specific managed session context. |
| `create_if_missing` | No | Auto-creates session to reduce caller orchestration overhead. |
| `stream` | No | Streams tokens for responsive interfaces on long responses. |
| `streaming_funct` | No | Callback sink for streamed tokens in custom frontends/CLIs. |
| `generate_walkthrough` | No | Adds concise run summary for audit/debug use cases. |
| `**kwargs` | No | Pass-through for advanced provider/runtime options. |

---

## Response Params

```python
str | None  # final assistant message
```

The method usually returns a final response string.
`None` indicates no final assistant message was produced (for example, interrupted or unresolved flows).
Handle this explicitly in production callers with retry/fallback logic.

---

## Examples

### 1) Basic MCPAgent Chat

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(provider="ollama", model="qwen2:7b")
response = await agent.chat("Summarize Python async in simple terms")
print(response)
```

This is the minimal MCPAgent bootstrapping pattern.
It is ideal when you want enterprise session behavior without custom server setup yet.
Start here, then add MCP configs and governance callbacks incrementally.

### 2) Session-aware Workflow

```python
agent = MCPAgent(provider="ollama", session_timeout=1800)
agent.create_session("team-1", metadata={"owner": "backend-team"})

await agent.chat("Store design decisions from this discussion", session_id="team-1")
response = await agent.chat("Recap our decisions", session_id="team-1")
print(response)
```

This example demonstrates explicit session lifecycle management.
Session metadata helps tagging and tracing conversations by team, tenant, or workflow.
Use named sessions to isolate business contexts and keep conversation history organized.

### 3) Deferred Tool Loading (Large Toolsets)

```python
agent = MCPAgent(
    provider="openai",
    model="gpt-4o-mini",
    deferred_tools=True,
    tool_threshold=15,
    debug=True
)

response = await agent.chat("Search file-related tools and read config files")
print(response)
```

This setup is for high-tool-count environments where loading all tools at once is expensive.
Deferred loading narrows active tools dynamically, improving scalability and often lowering latency.
Use this when integrating multiple MCP servers or very large tool catalogs.
