---
title: Memory
description: How Logicore stores and retrieves context across sessions.
---

# Memory

Memory keeps agents consistent across conversations and tool calls.

## What is stored
- User queries and agent responses (session transcript)
- Retrieved facts with embeddings
- Metadata: source, timestamps, confidence

## Configure memory
```python
from logicore import Agent
from logicore.memory import ProjectMemory

memory = ProjectMemory(path="./data/memory.db", top_k=8)
agent = Agent(memory=memory)
```

## Retrieval flow
1. Embed new facts and persist.
2. On each query, fetch top-k relevant entries.
3. Inject retrieved snippets into the prompt.

## Good hygiene
- Cap `top_k` to control prompt size.
- Prune stale entries periodically.
- Keep memory files out of version control.
- Redact secrets before storing.
