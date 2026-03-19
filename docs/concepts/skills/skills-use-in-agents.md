---
title: Use Custom Skills in Agents
description: Load custom skills by name or object across Agent types.
---
You can load skills at initialization time or dynamically after creating an agent.

---

## 1) Load by Name (String)

When passing a string, the agent resolves it from:
1. Package defaults (`logicore/skills/defaults`)
2. Workspace skill directories (if `workspace_root` is set)

```python
from logicore.agents.agent import Agent

agent = Agent(
    llm="ollama",
    tools=True,
    workspace_root="D:/Scratchy",
    skills=["release_assistant"]
)
```

---

## 2) Load a Skill Object Directly

```python
from logicore.skills.loader import SkillLoader
from logicore.agents.agent import Agent

skill = SkillLoader.load("D:/Scratchy/.agent/skills/release_assistant")

agent = Agent(llm="ollama", tools=True)
if skill:
    agent.load_skill(skill)
```

---

## 3) Load Multiple Skills

```python
agent.load_skills([
    "release_assistant",
    "web_research"
])
```

---

## 4) Using Skills with Other Agent Types

`BasicAgent`, `SmartAgent`, and `MCPAgent` all pass through the same underlying skill-loading flow from `Agent`.

```python
from logicore.agents.agent_mcp import MCPAgent

agent = MCPAgent(
    provider="ollama",
    mcp_config_path="mcp.json",
    workspace_root="D:/Scratchy",
    skills=["release_assistant"]
)
```

---

## What Happens After Loading

- Skill instructions are appended into the system prompt inside `<skills> ... </skills>`.
- Skill tools are added to `internal_tools` and become callable in normal chat.
- Matching executors are registered in `skill_tool_executors` for runtime execution.
