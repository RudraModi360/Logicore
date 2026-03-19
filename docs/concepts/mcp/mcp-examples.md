---
title: MCP Examples
description: Real examples for normal mode, deferred mode, and large/small toolset handling.
---

# MCP Examples

---

## 1) Small Toolset: Load Everything Normally

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(
    provider="ollama",
    model="qwen2:7b",
    mcp_config_path="mcp.json",
    tool_threshold=15  # default
)

# If total tools < 15, all tools are exposed normally
await agent.init_mcp_servers()
response = await agent.chat("List available file tools")
print(response)
```

---

## 2) Large Toolset: Auto Deferred Mode

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(
    provider="openai",
    model="gpt-4o-mini",
    mcp_config_path="mcp.json",
    tool_threshold=15,
    debug=True
)

# If total tools >= 15, deferred mode auto-enables
await agent.init_mcp_servers()
response = await agent.chat("Find tools for reading docs and summarize README")
print(response)
```

In this mode, the model first uses `tool_search_regex` to discover relevant tools.

---

## 3) Force Deferred Mode Even for Small Catalogs

```python
agent = MCPAgent(
    provider="ollama",
    deferred_tools=True,
    tool_threshold=15,
    mcp_config_path="mcp.json"
)

await agent.init_mcp_servers()
response = await agent.chat("Search tools related to csv and parse a sample")
print(response)
```

---

## 4) Lazy Initialization on First Chat

```python
agent = MCPAgent(provider="ollama", mcp_config_path="mcp.json")

# No explicit init call needed; first chat triggers lazy init
response = await agent.chat("Use MCP tools to inspect project files")
print(response)
```

---

## 5) Session Utilities for Production

```python
agent = MCPAgent(provider="ollama", session_timeout=1800)

agent.create_session("team-a", metadata={"tenant": "acme"})
await agent.chat("Store decisions from today", session_id="team-a")
summary = await agent.chat("Recap all decisions", session_id="team-a")
print(summary)

# Optional stale cleanup
deleted = agent.cleanup_stale_sessions()
print(f"Cleaned sessions: {deleted}")
```

These examples use the same `chat()` API while scaling from simple to enterprise MCP scenarios.
