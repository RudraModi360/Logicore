---
title: Execution Hooks
description: Intercept and customize every point in the agent execution pipeline.
---

# Execution Hooks

Hooks let you tap into Logicore's execution pipeline — before an LLM call, after a response, around tool execution — without modifying agent or provider code.

---

## Hook Points

Eight interception points are available, executed in this order each turn:

```
BEFORE_MODEL         ← modify messages/tools before the LLM call
     │
     ▼
   LLM call
     │
AFTER_MODEL          ← inspect/modify the response; can synthesize instead
     │
BEFORE_TOOL_SELECTION ← change which tools are visible
     │
AFTER_TOOL_SELECTION  ← inspect the chosen tool calls
     │
BEFORE_TOOL_EXECUTION ← validate/replace tool arguments
     │
  tool executes
     │
AFTER_TOOL_EXECUTION  ← process results before they go back to the LLM
     │
BEFORE_CONTEXT_COMPRESSION ← influence what gets summarised
     │
AFTER_TURN           ← fires after a full round-trip (including tools)
```

---

## Quick Start

```python
from logicore.runtime.hooks import HookSystem, HookPoint, HookContext, HookResult, HookAction

hooks = HookSystem()

@hooks.register(HookPoint.BEFORE_MODEL, priority=10)
async def inject_date(ctx: HookContext) -> HookResult:
    """Prepend today's date to every LLM call."""
    from datetime import date
    msg = {"role": "system", "content": f"Today is {date.today()}."}
    return HookResult(action=HookAction.MODIFY, modified_messages=[msg] + ctx.messages)
```

Hooks can be **async** (preferred) or **sync**.

---

## Input Params

### `HookContext`

Passed to every hook. Fields populated depend on the hook point.

| Field | Type | Populated at |
|---|---|---|
| `hook_point` | `HookPoint` | Always |
| `messages` | `list` | All hooks |
| `tools` | `list` | BEFORE_MODEL, tool hooks |
| `model_response` | `NormalizedMessage` | AFTER_MODEL and later |
| `tool_calls` | `list` | AFTER_MODEL, tool hooks |
| `tool_name` | `str` | BEFORE/AFTER_TOOL_EXECUTION |
| `tool_args` | `dict` | BEFORE/AFTER_TOOL_EXECUTION |
| `tool_result` | `Any` | AFTER_TOOL_EXECUTION |
| `session_id` | `str` | Always (if set on agent) |
| `turn_number` | `int` | Always |
| `metadata` | `dict` | User-defined pass-through |

### `HookResult`

Return this from every hook.

| Field | Type | Meaning |
|---|---|---|
| `action` | `HookAction` | What to do next |
| `modified_messages` | `list` | Replacement messages (MODIFY only) |
| `modified_tools` | `list` | Replacement tools (MODIFY only) |
| `modified_tool_calls` | `list` | Replacement tool calls (MODIFY only) |
| `modified_tool_args` | `dict` | Replacement args (MODIFY only) |
| `synthesized_response` | `NormalizedMessage` | Skip LLM, use this (SYNTHESIZE only) |
| `skip_reason` | `str` | Logged when action == SKIP |
| `metadata` | `dict` | Merged into aggregated result |

---

## Response Params

Each `hooks.execute()` call returns an aggregated `HookResult`. Your code reads:

```python
result = await hooks.execute(HookPoint.BEFORE_MODEL, ctx)

if result.action == HookAction.SYNTHESIZE:
    return result.synthesized_response   # skip the LLM entirely

if result.action == HookAction.MODIFY and result.modified_messages:
    messages = result.modified_messages  # use updated messages
```

---

## Hook Actions

| Action | Effect |
|---|---|
| `CONTINUE` | Default — proceed normally |
| `MODIFY` | Replace messages / tools / args with modified versions |
| `SKIP` | Skip the current operation (e.g. skip this tool call) |
| `SYNTHESIZE` | Return `synthesized_response` as the LLM answer (BEFORE_MODEL only) |
| `RETRY` | Retry the current operation |
| `ABORT` | Abort the entire turn |

When multiple hooks run, results are aggregated:
- First `SYNTHESIZE` / `SKIP` / `ABORT` wins and stops further hooks.
- All `MODIFY` results are merged (later hooks see earlier modifications).
- `metadata` dicts are merged from all hooks.

---

## Examples

### 1) Cache responses (SYNTHESIZE)

```python
from logicore.runtime.hooks import HookSystem, HookPoint, HookContext, HookResult, HookAction
from logicore.providers.gateway import NormalizedMessage

hooks = HookSystem()
cache = {}

@hooks.register(HookPoint.BEFORE_MODEL, priority=1)
async def cache_hook(ctx: HookContext) -> HookResult:
    key = str(ctx.messages)
    if key in cache:
        return HookResult(
            action=HookAction.SYNTHESIZE,
            synthesized_response=NormalizedMessage(role="assistant", content=cache[key])
        )
    return HookResult()

@hooks.register(HookPoint.AFTER_MODEL)
async def fill_cache(ctx: HookContext) -> HookResult:
    if ctx.model_response:
        cache[str(ctx.messages)] = ctx.model_response.content
    return HookResult()
```

### 2) Block dangerous tools (SKIP)

```python
BLOCKED_TOOLS = {"delete_file", "drop_table"}

@hooks.register(HookPoint.BEFORE_TOOL_EXECUTION, priority=1)
async def safety_guard(ctx: HookContext) -> HookResult:
    if ctx.tool_name in BLOCKED_TOOLS:
        return HookResult(action=HookAction.SKIP, skip_reason="blocked by policy")
    return HookResult()
```

### 3) Enrich tool arguments (MODIFY)

```python
@hooks.register(HookPoint.BEFORE_TOOL_EXECUTION)
async def inject_user_id(ctx: HookContext) -> HookResult:
    args = dict(ctx.tool_args or {})
    args["user_id"] = ctx.metadata.get("user_id", "anonymous")
    return HookResult(action=HookAction.MODIFY, modified_tool_args=args)
```

### 4) Observe every turn (CONTINUE)

```python
@hooks.register(HookPoint.AFTER_TURN)
async def audit_log(ctx: HookContext) -> HookResult:
    import json, logging
    logging.info(json.dumps({
        "turn": ctx.turn_number,
        "session": ctx.session_id,
        "tool_calls": len(ctx.tool_calls),
    }))
    return HookResult()
```

---

## Priority

Lower priority numbers execute first.

```python
hooks.add_hook("critical", HookPoint.BEFORE_MODEL, critical_fn, priority=1)
hooks.add_hook("normal",   HookPoint.BEFORE_MODEL, normal_fn,   priority=50)
hooks.add_hook("last",     HookPoint.BEFORE_MODEL, last_fn,     priority=200)
```

---

## Managing Hooks

```python
# Enable / disable without removing
hooks.enable_hook("audit_log", enabled=False)

# Remove entirely
hooks.remove_hook("audit_log")
hooks.remove_hook("audit_log", hook_point=HookPoint.AFTER_TURN)  # specific point only

# Inspect
hooks.get_hooks(HookPoint.BEFORE_MODEL)   # list[HookRegistration]
hooks.get_all_hooks()                      # dict[HookPoint, list[HookRegistration]]

# Execution stats
hooks.get_stats()
# {"inject_date": {"executions": 42, "successes": 42, "failures": 0, "total_time": 0.012}}

hooks.reset_stats()
```

---

## Notes

- Hooks that raise exceptions are **isolated by default** — a failing hook logs a warning and execution continues. Pass `fail_fast=True` to `HookSystem()` to raise instead.
- Sync hooks run in a thread executor so they don't block the async loop.
- Each `execute()` call creates a fresh aggregated result; hooks share context within a single call only.
