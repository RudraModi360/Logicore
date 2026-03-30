---
title: Dynamic Tool Loading (Large vs Small Toolsets)
description: How MCPAgent chooses between full tool loading and deferred tool discovery.
---
`MCPAgent` supports two strategies:
- **Normal mode**: expose all tools to the model
- **Deferred mode**: expose only a search tool first, then load tools on demand

This decision is based on `deferred_tools` and `tool_threshold`.

---

## Decision Rules

### Case 1: Small tool count (below threshold)

If total tools are less than `tool_threshold` (default `15`):
- Agent stays in normal mode
- All tools are sent to the model directly
- No dynamic discovery step is required

### Case 2: Large tool count (at or above threshold)

If total tools are greater than or equal to `tool_threshold`:
- Agent auto-enables deferred mode
- Agent registers all tools in an internal registry
- Model initially gets `tool_search_regex` plus already-loaded tools
- Tools discovered through search are marked loaded and become callable

### Case 3: Explicit deferred mode

If you set `deferred_tools=True`:
- Deferred mode is enabled regardless of tool count
- Useful when you always want controlled, incremental tool exposure

---

## Why Deferred Mode Helps

With many tools, sending every schema each turn can hurt latency and context budget.

Deferred loading keeps the active toolset small:
1. Model searches tools by regex
2. Matching tools are loaded
3. Model calls only those relevant tools

---

## Internal Behavior Summary

- Registry: `_tool_registry` stores all known schemas
- Loaded set: `_loaded_tools` stores currently exposed tools
- Search tool: `tool_search_regex(pattern, limit)` finds and loads tools
- Auto switch: enabled when total tools `>= tool_threshold`

---

## Practical Recommendations

- Keep default threshold unless you have strong latency/context constraints
- Use explicit deferred mode for very large, constantly-changing tool ecosystems
- Keep tool names/descriptions clear so regex discovery is effective
