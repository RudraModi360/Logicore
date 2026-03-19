---
title: Introduction to Agents
description: Choose and configure your agent type for any use case.
---

# Agents: The Core of Logicore

An **Agent** is an autonomous AI worker that:
- Understands natural language tasks
- Decides which tools to use (if any)
- Maintains context across multi-turn conversations
- Executes actions safely with approval workflows

---

## How It Works

Agents operate in a continuous loop:

1. **Receive** user input
2. **Decide** whether to call a tool
3. **Approve** tool execution (if needed)
4. **Execute** the tool and capture results
5. **Synthesize** a final response
6. **Repeat** until done (or max iterations reached)

[See detailed architecture](./agents-overview) with execution diagrams and internal flows.

---

## Production Chat API Contract

Use this section as the authoritative request/response contract for chat calls across Logicore agents.

### Request Parameters (Detailed)

| Parameter | Type | Required | Description |
|---------|------|----------|-------------|
| `message` | `str` | ✓ Yes | Primary user instruction. This should be explicit, task-scoped, and unambiguous. In production, prefer intent + constraints + expected output shape in one prompt (for example: "Summarize this log in 5 bullets with severity tags"). Empty strings should be treated as invalid input by the caller. |
| `callbacks` | `dict` | No | Runtime hooks for streaming and lifecycle observability. Common callback is `{"on_token": callable}` for token streaming. In advanced flows, callbacks may also include tool lifecycle/approval hooks depending on agent type. Keep callback functions fast and non-blocking to avoid degrading token throughput. |
| `stream` | `bool` | No | Enables incremental token emission. Set `True` for chat UIs or long outputs to reduce perceived latency. For deterministic backend jobs, set `False` and wait for final response. If `stream=True` without a valid token callback, output is still generated but you lose progressive rendering benefits. |
| `temperature` | `float` | No | Per-request creativity control. Lower values (near `0.0`) improve consistency and reduce variance; higher values increase diversity but may reduce reliability for strict tasks. Use low temperature for extraction, classification, and policy workflows; use moderate values for ideation or drafting. |
| `max_tokens` | `int` | No | Upper bound for response length. Use it to protect cost/latency budgets and avoid overlong outputs. If too low, responses may truncate before task completion. In production, set task-specific ceilings (for example small for labels, larger for summaries). |
| `metadata` | `dict` | No | Caller-supplied context envelope for traceability (for example request IDs, tenant, feature flag, route, experiment id). Treat this as observability metadata, not prompt content. Do not place secrets or regulated payloads unless your telemetry/storage policy explicitly allows it. |

### Example Request (Production Style)

```python
response = await agent.chat(
    message="Summarize this incident timeline into 6 bullets with severity and owner.",
    callbacks={"on_token": on_token},
    stream=True,
    temperature=0.2,
    max_tokens=400,
    metadata={
        "request_id": "req_9f21",
        "tenant": "acme-prod",
        "feature": "incident-summary"
    }
)
```

### Response Contract

- Most Logicore `chat()` calls return the final assistant output as a `str`.
- The response may be partial/truncated when `max_tokens` is too restrictive.
- If tools are involved, internal tool steps occur before the final string is returned.
- Error handling should be implemented at caller level with retries/timeouts around `chat()`.

### Operational Guidance

- **For reliability:** `temperature=0.0–0.3`, bounded `max_tokens`, explicit prompt format.
- **For UX:** `stream=True` + `on_token` callback.
- **For compliance:** include `metadata.request_id` and avoid sensitive fields in metadata.
- **For performance:** keep callbacks lightweight; avoid blocking I/O in callback handlers.

---

## Choose Your Agent Type

Logicore provides three agent types, each optimized for different needs:

### BasicAgent: Fastest to Prototype
- **Best for:** Learning, simple chatbots, quick experiments
- **What it has:** Chat, multi-turn memory, minimal config
- **What it lacks:** Tools, persistent memory, approval workflows
- **Setup time:** 2 minutes

```python
from logicore.agents.agent_basic import BasicAgent

agent = BasicAgent(llm="ollama")
response = await agent.chat("What is AI?")
```

[BasicAgent Guide](./basic-agent) | [API Docs](#)

---

### Agent: Customized Production-Ready Standard
- **Best for:** Most real-world applications with tools
- **What it has:** Tools, persistent memory, approval workflows, streaming
- **What it lacks:** Built-in tools, project context, MCP server support
- **Setup time:** 5 minutes

```python
from logicore.agents.agent import Agent

def check_weather(location: str) -> str:
    """Get weather for a location."""
    return "72°F and sunny"

agent = Agent(llm="ollama", tools=[check_weather])
agent.set_auto_approve_all(True)
response = await agent.chat("What's the weather in Seattle?")
```

[Agent Guide](./full-agent) | [API Docs](#)

---

### SmartAgent: Versatile with Built-in Tools
- **Best for:** Project-aware development, dual-mode work, built-in web/bash tools[multimedia(Images/Videos supported) web search]
- **What it has:** All Agent features + web search, bash, notes, project memory, duo modes
- **What it lacks:** MCP server support (choose MCPAgent for that)
- **Setup time:** 5 minutes

```python
from logicore.agents.agent_smart import SmartAgent

# Solo mode: explore and reason
agent = SmartAgent(llm="ollama", mode="solo")
response = await agent.chat("Find the latest AI trends and explain")

# Or Project mode: focused work
agent = SmartAgent(llm="ollama", mode="project")
agent.create_project("api-backend", "REST API", goal="Build scalable API")
agent.switch_to_project("api-backend")
response = await agent.chat("Design the authentication flow")
```

[SmartAgent Guide](./smart-agent) | [API Docs](#)

---

### MCPAgent: Enterprise-Scale
- **Best for:** Large teams, many tools, compliance requirements
- **What it has:** MCP servers, hierarchical tool governance, audit trails
- **What it lacks:** Nothing - this is the complete package
- **Setup time:** 10 minutes

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(
    llm="ollama",
    mcp_servers=["file_system", "web_search", "database_client"]
)
agent.set_callbacks(on_tool_approval=rbac_approval)
response = await agent.chat("Find AI trends and save to database")
```

[MCPAgent Guide](../mcp/mcp-agent) | [API Docs](#)

---

## Quick Comparison

| Feature | BasicAgent | Agent | SmartAgent | MCPAgent |
|---------|-----------|-------|-----------|----------|
| **Chatbot Mode** | ✓ | ✓ | ✓ | ✓ |
| **Tool Execution** | | ✓ | ✓ | ✓ |
| **Auto-Schema Tools** | | ✓ | ✓ | ✓ |
| **Built-in Tools** | | | ✓ | ✓ |
| **Web Search** | | | ✓ | ✓
| **Bash Execution** | | | ✓ | ✓ |
| **Cron Scheduling** | | ✓ | ✓ | ✓ |
| **Persistent Memory** | | ✓ | ✓ | ✓ |
| **Approval Workflows** | | ✓ | ✓ | ✓ |
| **Streaming** | | ✓ | ✓ | ✓ |
| **Project Mode** | | | ✓ | |
| **Skills** | | ✓ | ✓ | ✓ |
| **MCP Servers** | | | | ✓ |
| **RBAC & Governance** | | | | ✓ |
| **Audit Trails** | | | | ✓ |

---

## Agent Anatomy

Every agent has three core components:

### Brain: The LLM Provider
```python
# Choose your provider
agent = Agent(llm="ollama")       # Local
agent = Agent(llm="openai")       # Cloud
agent = Agent(llm="gemini")       # Google
agent = Agent(llm="groq")         # Fast inference
agent = Agent(llm="azure")        # Enterprise
```

### Hands: Tools & Skills
```python
# Register Python functions as tools
def analyze_sentiment(text: str) -> str:
    return "positive" if "good" in text else "negative"

agent = Agent(tools=[analyze_sentiment])

# Or load pre-built skills
agent.load_skill("web_research")
agent.load_skill("code_review")
```

### Memory: Context & Knowledge
```python
# Session memory (automatic)
await agent.chat("My favorite color is blue")
await agent.chat("What's my favorite?")  # Remembers

# Persistent memory (optional)
agent = Agent(memory=True)  # Stores facts across sessions
```

---

## Going Deeper

### Understand internals
- [How agents work internally](./agents-overview) — Execution flow, architecture, error handling
- [Provider gateway pattern](../providers/providers-overview) — How providers are abstracted

### Agent-specific guides
- [BasicAgent guide](./basic-agent) — Minimal config, perfect for learning
- [Agent guide](./full-agent) — Tools, memory, approval, production-ready
- [SmartAgent guide](./smart-agent) — Project-aware, built-in tools, dual modes
- [MCPAgent guide](../mcp/mcp-agent) — MCP servers, governance, enterprise features

### Related concepts
- [Skills](../skills/skills) — Pre-built tool packs
- [Memory](../memory/memory) — Session and persistent memory systems
- [Tools](../tools/tools) — Creating and managing tools
- [Providers](../providers/providers) — Multi-provider LLM support

---

## Best Practices

- **Keep system prompts focused:** Tell agent its role, not everything it can do
- **Register only necessary tools:** Fewer choices = faster, clearer decisions
- **Enable streaming for UIs:** Real-time token feedback improves UX
- **Use approval callbacks:** Never blindly auto-approve dangerous tools
- **Monitor for loops:** Set appropriate `max_iterations` (default: 5)
- **Log everything in production:** Use callbacks for audit trails and debugging

---

## Common Scenarios

### Scenario 1: Quick Chatbot
→ Use **BasicAgent**
```python
agent = BasicAgent(llm="ollama")
# Done. No tools, no config needed.
```

### Scenario 2: Weather Assistant with Web Search
→ Use **Agent**
```python
agent = Agent(llm="ollama", tools=[check_weather, search_web])
agent.set_auto_approve_all(True)
```

### Scenario 3: Developer Assistant for a Project
→ Use **SmartAgent**
```python
agent = SmartAgent(llm="ollama", mode="project")
agent.create_project("my-app", "Python App", goal="Build CLI tool")
agent.switch_to_project("my-app")
# Web search, bash, notes, memory all built-in
```

### Scenario 4: Enterprise Data Pipeline with Custom MCP Tools
→ Use **MCPAgent**
```python
agent = MCPAgent(
    llm="azure",
    mcp_servers=["database", "file_system", "http_client"]
)
agent.set_callbacks(on_tool_approval=rbac_approval)
```

---

## Next Steps

1. [See how agents work internally](./agents-overview) — Visual architecture overview
2. [Pick your agent type and learn it](#choose-your-agent-type)
3. [Build your first agent in 5 minutes](../../quickstart)
4. [Explore advanced patterns](./agents-overview#multi-turn-conversations)
