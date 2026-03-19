---
title: MCP in Logicore
description: Core concept guide to MCP, server integration, and dynamic tool loading.
---
MCP (Model Context Protocol) in Logicore lets your agent connect to external tool servers and use those tools during chat.

At a high level, MCP gives you:
- A standard way to connect external tools
- Safe tool execution through the same approval model as internal tools
- Better scalability for large tool catalogs through deferred loading

---

## What MCP Adds Beyond Normal Tools

- **External tool servers** via `mcp.json` configuration
- **Tool discovery** across connected servers
- **Session-aware chat** with MCP tool execution
- **Dynamic tool exposure** when tool count is large

---

## MCP Concept Map

```mermaid
graph TD
    USER[User Request] --> AGENT[MCPAgent]
    AGENT --> MGR[MCPClientManager]
    MGR --> CFG[mcp.json]
    MGR --> S1[MCP Server A]
    MGR --> S2[MCP Server B]
    S1 --> TOOLS1[Tool Set]
    S2 --> TOOLS2[Tool Set]
    AGENT --> EXEC[Tool Execution Loop]
    EXEC --> RESP[Final Response]
```

---

## Read Next

- [MCP Overview](./mcp-overview)
- [How MCP Works](./mcp-how-it-works)
- [Dynamic Tool Loading](./mcp-dynamic-tools)
- [MCP Examples](./mcp-examples)
