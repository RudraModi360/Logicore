---
title: SimpleMem Integration for Persistence
description: Integrate and configure AgentrySimpleMem for durable memory across runs and sessions.
---
AgentrySimpleMem provides persistent vector memory backed by LanceDB.

---

## Default Integration in Agent

When you create an agent with memory enabled, Agent instantiates SimpleMem:

```python
agent = Agent(
    llm="ollama",
    memory=True
)
```

Internally, this creates an AgentrySimpleMem instance and wires it into chat flow.

---

## Important Default Behavior

By default, SimpleMem uses isolate_by_session=True.

That means each session_id maps to a separate table:
- `memories_<user>_<session>`

So persistence exists across process restarts for that session table, but memory is isolated between different session IDs unless reconfigured.

---

## Enable Cross-Session Shared Memory

If you want one persistent memory pool across sessions for the same user/role, replace the default SimpleMem instance:

```python
from logicore.agents.agent import Agent
from logicore.simplemem import AgentrySimpleMem

agent = Agent(llm="ollama", memory=True)

# Replace with shared-table configuration
agent.simplemem = AgentrySimpleMem(
    user_id=agent.role,
    session_id="global",
    isolate_by_session=False,
    debug=True
)

# Chats from different session IDs now write/read from one user table
await agent.chat("My preferred stack is FastAPI + PostgreSQL", session_id="s1")
await agent.chat("What stack do I prefer?", session_id="s2")
```

---

## Storage Location

LanceDB path is configurable:
- local mode default: logicore/user_data/lancedb_data
- cloud mode: set LANCEDB_PATH

Useful environment variables:
- AGENTRY_MODE
- LANCEDB_PATH
- EMBEDDING_MODEL
- OLLAMA_URL
- SIMPLEMEM_MIN_STORE_SCORE
- SIMPLEMEM_MIN_RETRIEVE_SCORE

---

## Flush and Cleanup

Memory queue is flushed:
- after assistant final response in chat
- during agent cleanup

This ensures queued memory entries are persisted.
