---
title: Agents
description: What a Logicore agent is made of and how it behaves.
---

# Understanding Agents

An **Agent** is an autonomous worker that understands tasks, decides which tools to use, and keeps context across interactions.

## Agent anatomy
- **Brain:** LLM provider + reasoning settings
- **Hands:** Tools, skills, workflows
- **Memory:** Sessions and retrieved facts that ground responses

## Basic example
```python
from logicore import Agent
agent = Agent()
response = agent.chat("Hello! What can you do?")
```

## Agent types
- **BasicAgent:** Simplest entry point for quick prototypes.
- **Agent:** Full-featured with tools, memory, and providers.
- **MCPAgent:** Enterprise-scale with many tools and approval hooks.

## Best practices
- Keep the system prompt short and outcome-focused.
- Register only the tools needed for the task to reduce confusion.
- Enable streaming for chat UIs; use async when running multiple agents.
- Log tool calls and provider latency for debugging and routing.
