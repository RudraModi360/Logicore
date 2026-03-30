---
title: MCP Overview
description: Understand when to use MCP and what problems it solves in Logicore.
---
Use MCP when your agent needs tools that are not only local Python functions.

MCP enables `MCPAgent` to connect to one or many external servers, collect their tool schemas, and call those tools in normal chat flows.

---

## When to Use MCP

Use MCP when you need:
- Shared tool servers used by multiple teams
- Enterprise governance around external tools
- Very large tool ecosystems that can exceed prompt/tool limits
- A standard protocol for non-local tool execution

For small local-only projects, `Agent` or `SmartAgent` may be enough.

---

## Main Components

- **`MCPAgent`**: Agent layer with sessions, tool routing, and deferred loading logic
- **`MCPClientManager`**: Connects to servers in `mcp.json` and maps tools to servers
- **`mcp.json`**: Declares MCP servers, commands, args, and env
- **Tool map**: Internal lookup from `tool_name -> server_name`

---

## Lifecycle Summary

1. Create `MCPAgent`
2. Optionally call `init_mcp_servers()` (or allow lazy init on first chat)
3. Agent merges internal + MCP tools
4. Agent executes chat loop with tool calls
5. Manager routes external calls to the right MCP server

---

## Why This Matters

MCP lets Logicore scale from a few local tools to enterprise tool catalogs without changing your chat API.
