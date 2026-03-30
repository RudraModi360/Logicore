---
title: Short-Term Memory Handling
description: How Logicore handles per-session conversation memory.
---
Short-term memory is the in-session message history maintained by the agent.

## Where It Lives

Short-term memory is stored in AgentSession:
- session_id
- messages (system, user, assistant, tool outputs)
- timestamps and metadata

Each session has isolated conversation context.

---

## How It Works During Chat

1. Agent resolves or creates the session by session_id.
2. Incoming user message is appended to session.messages.
3. LLM receives the accumulated session history.
4. Assistant response and tool outputs are appended back into the same history.

This gives continuity within the same session.

---

## Session APIs

```python
from logicore.agents.agent import Agent

agent = Agent(llm="ollama")

# Default session
await agent.chat("Hello")

# Explicit session
await agent.chat("Plan sprint tasks", session_id="team-a")

# Inspect session
session = agent.get_session("team-a")
print(len(session.messages))

# Clear session history (keep system message)
agent.clear_session("team-a")
```

---

## Notes

- Session memory is runtime context for active conversations.
- Different session IDs do not share message history.
- Clearing a session resets short-term memory for that session.
