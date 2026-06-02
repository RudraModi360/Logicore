---
title: Hooks
description: Overview of Logicore's execution hook system and available guides.
---

# Hooks

The Logicore hook system lets you intercept and customise any point in the agent execution pipeline — without touching agent or provider code.

---

## Guides

- [Hooks Overview](./hooks-overview.md) — All hook points, actions, context fields, and examples

---

## When to use hooks

| Use case | Hook point |
|---|---|
| Inject date / tenant / user context into every LLM call | `BEFORE_MODEL` |
| Cache LLM responses | `BEFORE_MODEL` (check cache) + `AFTER_MODEL` (fill cache) |
| Auto-escalate reasoning on complex responses | `AFTER_MODEL` |
| Block or modify tool calls before execution | `BEFORE_TOOL_EXECUTION` |
| Audit / log tool results | `AFTER_TOOL_EXECUTION` |
| Compress context on your own schedule | `BEFORE_CONTEXT_COMPRESSION` |
| Emit analytics after every turn | `AFTER_TURN` |

---

## Quick example

```python
from logicore.runtime.hooks import HookSystem, HookPoint, HookContext, HookResult, HookAction

hooks = HookSystem()

@hooks.register(HookPoint.BEFORE_MODEL, priority=10)
async def inject_tenant(ctx: HookContext) -> HookResult:
    tenant = ctx.metadata.get("tenant_id", "default")
    msg = {"role": "system", "content": f"Tenant: {tenant}"}
    return HookResult(action=HookAction.MODIFY, modified_messages=[msg] + ctx.messages)
```

See [Hooks Overview](./hooks-overview.md) for the full reference including all actions, priorities, and aggregation rules.
