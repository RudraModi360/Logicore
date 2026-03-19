---
title: Introduction to Memory
description: Core concept guide to Memory in Logicore.
---
Memory in Logicore combines short-term conversation context with optional persistent memory.

At a high level, memory gives you:
- Per-session chat continuity through session history
- Optional durable memory through SimpleMem vector storage
- Retrieval-ready context when relevant to a new query

---

## Memory Concept Map

```mermaid
graph TD
	USER[User Message] --> AGENT[Agent]
	AGENT --> STM[Short-Term Memory<br/>Session messages]
	AGENT --> SIMPLEMEM[SimpleMem Queue + Retrieval]
	SIMPLEMEM --> LANCE[LanceDB Vector Store]
	LANCE --> RET[Semantic Retrieval]
	RET --> AGENT
	AGENT --> RESP[Assistant Response]
```

---

## Read Next

- [Memory Overview](./memory-overview)
- [Short-Term Memory Handling](./memory-short-term)
- [Long-Term Memory Handling](./memory-long-term)
- [SimpleMem Integration for Persistence](./memory-simplemem-integration)
- [Use Memory in Agents](./memory-use-in-agents)
