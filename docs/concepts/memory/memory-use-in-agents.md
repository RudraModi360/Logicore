---
title: Use Memory in Agents
description: Practical examples for enabling and using memory in Agent, BasicAgent, and MCPAgent.
---
Memory is enabled through the memory flag in all main agent types.

---

## 1) Agent

```python
from logicore.agents.agent import Agent

agent = Agent(
    llm="ollama",
    memory=True,
    tools=True
)

await agent.chat("I work in the payments team", session_id="team-payments")
reply = await agent.chat("What team am I in?", session_id="team-payments")
print(reply)
```

---

## 2) BasicAgent

```python
from logicore.agents.agent_basic import BasicAgent

agent = BasicAgent(
    provider="ollama",
    memory_enabled=True
)

response = await agent.chat("Remember that I prefer concise responses")
print(response)
```

---

## 3) MCPAgent

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(
    provider="ollama",
    mcp_config_path="mcp.json",
    memory=True
)

await agent.init_mcp_servers()
response = await agent.chat("Remember project codename is Atlas", session_id="ops")
print(response)
```

---

## 4) Inspect Memory Stats

```python
if agent.simplemem:
    stats = agent.simplemem.get_stats()
    print(stats)
```

---

## 5) Force Persist Pending Queue

```python
if agent.simplemem:
    await agent.simplemem.process_pending()
```

---

## Best Practices

- Keep session IDs stable for workflows that need continuity.
- Use shared table mode only when cross-session memory sharing is desired.
- Avoid storing secrets or regulated data unless governance controls are in place.
