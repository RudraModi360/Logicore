---
title: MCPAgent
description: Enterprise-scale agents with custom MCP tools and advanced governance.
---

# MCPAgent

**MCPAgent** is built for enterprise environments. It supports custom Model Context Protocol (MCP) servers as tool suites, advanced governance with audit trails, role-based access control, and large-scale tool management—enabling hundreds of tools with built-in compliance.

---

## When to Use MCPAgent

- Enterprise applications with many custom tools
- Integrating external services via MCP servers
- Advanced tool governance and audit trails
- Large teams with role-based access control
- Compliance and regulatory requirements

---

## Quick Start

```python
from logicore.agents.agent_mcp import MCPAgent
import asyncio

async def main():
    agent = MCPAgent(
        llm="ollama",
        mcp_servers=["file_system", "web_search"]
    )
    agent.set_auto_approve_all(True)
    
    response = await agent.chat("Search for AI trends 2024")
    print(response['content'])

asyncio.run(main())
```

---

## How It Works

MCPAgent loads tools from MCP (Model Context Protocol) servers. Each server provides a suite of related tools (e.g., file_system server provides read_file, write_file, delete_file). You can load built-in servers or connect custom servers. Every tool execution is recorded with approvals, making it audit-compliant.

**Built-in MCP Servers:**

| Server | Tools | Use Case |
|--------|-------|----------|
| `file_system` | read, write, delete, list files | File operations |
| `web_search` | search, scrape, get links | Web research |
| `database_client` | query, insert, update, delete | Database access |
| `http_client` | GET, POST, PUT, DELETE | REST APIs |

---

## Configuration Parameters

### Constructor Parameters

```python
agent = MCPAgent(
    llm: str = "ollama",                    # ✓ Required: LLM provider
    model: str = None,                      # Specific model
    mcp_servers: List[str | Dict] = None,   # MCP servers to load
    tools: List[Callable] = None,           # Additional Python tools
    memory: bool = True,                    # Enable persistent memory
    debug: bool = False,                    # Enable logging
    temperature: float = 0.7,               # LLM randomness
    max_iterations: int = 20,               # Max tool loop iterations
    tool_governance_enabled: bool = True,   # Enable audit trails
    audit_logs_path: str = None,            # Where to store logs
    mcp_timeout_seconds: int = 30,          # MCP server timeout
    **kwargs
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | str | Required | Provider: `ollama`, `openai`, `gemini`, `groq`, `azure` |
| `model` | str | Provider default | Model name |
| `mcp_servers` | List | None | `["file_system", "web_search"]` or custom configs |
| `tools` | List | None | Additional Python functions |
| `memory` | bool | True | Enable persistent memory |
| `debug` | bool | False | Print execution details |
| `temperature` | float | 0.7 | Randomness (0-1) |
| `max_iterations` | int | 20 | Prevent infinite loops |
| `tool_governance_enabled` | bool | True | Enable audit/compliance |
| `audit_logs_path` | str | None | Where to save audit logs |
| `mcp_timeout_seconds` | int | 30 | MCP server timeout |

---

## Chat Method: Input & Output

### Request Parameters

```python
response = await agent.chat(
    message: str,                           # ✓ Required: Your prompt
    callbacks: Dict = None,                 # Optional: `{"on_token": fn}`
    stream: bool = False,                   # Optional: Enable streaming
    approve_all: bool = False,              # Optional: Auto-approve all
    approval_filter: Callable = None,       # Optional: Per-tool approval
    mcp_server_override: str = None,        # Optional: Force specific server
    record_audit_trail: bool = True,        # Optional: Log for compliance
    temperature: float = None,              # Optional: Override temperature
    max_tokens: int = None,                 # Optional: Max response length
    metadata: Dict = None                   # Optional: Additional context
)
```

### Response Schema

```python
{
    "role": "assistant",                    # Always "assistant"
    "content": str,                         # Final answer
    "tool_calls": List[Dict],               # Tools executed (MCP + Python)
    "tokens_used": int,                     # Total tokens
    "provider": str,                        # Provider used
    "model": str,                           # Model name
    "finish_reason": str,                   # "stop" or "max_tokens"
    "execution_steps": List[Dict],          # Step-by-step history
    "memory_updated": bool,                 # Was memory updated?
    "audit_trail_id": str,                  # Links to compliance log
    "mcp_servers_used": List[str],          # MCP servers invoked
    "governance_violations": List[Dict],    # Policy violations (if any)
    "metadata": dict                        # Timestamps, etc.
}
```

**MCP Tool Call Object:**
```python
{
    "id": "call_456",
    "name": "search",                       # Tool name
    "server": "web_search",                 # Which MCP server
    "arguments": {"query": "AI trends"},    # Inputs
    "result": "[search results]",           # Output
    "status": "success",                    # success/error
    "approved": True,                       # Was approved?
    "approval_metadata": {
        "requested_at": "2024-03-18T10:30:45Z",
        "approved_by": "system"
    },
    "execution_time_ms": 523,
    "governance_checked": True,
    "governance_status": "passed"
}
```

---

## Examples: Basic to Advanced

### Example 1: Simple MCP Tool Usage

```python
agent = MCPAgent(
    llm="ollama",
    mcp_servers=["web_search"]  # Load web search server
)
agent.set_auto_approve_all(True)

response = await agent.chat(
    "Search for machine learning frameworks and summarize"
)

print(response['content'])
print(f"MCP servers used: {response['mcp_servers_used']}")
print(f"Audit trail ID: {response['audit_trail_id']}")
```

**Output:**
```
The top machine learning frameworks are:
1. PyTorch - Dynamic computation graphs...
2. TensorFlow - Production-ready framework...
[Based on actual web search results]

MCP servers used: ['web_search']
Audit trail ID: audit_2024031810304567
```

---

### Example 2: Role-Based Access Control

```python
# Define permissions by role
TOOL_PERMISSIONS = {
    "viewer": ["file_system.read_file", "web_search.search"],
    "editor": [
        "file_system.read_file", 
        "file_system.write_file", 
        "web_search.search"
    ],
    "admin": ["*"]  # All tools
}

async def rbac_approval(tool_name, args):
    """Approve based on user role."""
    user_role = "editor"  # From session/auth
    allowed = TOOL_PERMISSIONS.get(user_role, [])
    
    if "*" in allowed:
        return True  # Admin: allow all
    
    return tool_name in allowed

agent = MCPAgent(
    llm="ollama",
    mcp_servers=["file_system", "web_search", "database_client"]
)
agent.set_callbacks(on_tool_approval=rbac_approval)

# Editor can read/write files but cannot delete
response = await agent.chat("List all files in /data/")
# ✓ Approved (read_file)

response = await agent.chat("Delete old logs in /data/")
# ✗ Denied (delete_file not in editor permissions)
```

---

### Example 3: Audit Trail & Compliance

```python
agent = MCPAgent(
    llm="openai",
    mcp_servers=["file_system", "database_client"],
    tool_governance_enabled=True,
    audit_logs_path="/var/log/agent_audit.log"
)

# Every tool execution is logged
response = await agent.chat(
    "Back up the user database"
)

# Retrieve audit trail
audit_logs = agent.get_audit_logs(
    time_period="2024-03-18",
    tool_filter="database_client"
)

for log in audit_logs:
    print(f"{log['timestamp']} - {log['tool']}: {log['result']}")

# Generate compliance report
report = {
    "period": "2024-03-18",
    "total_operations": len(audit_logs),
    "tools_used": set(log['tool'] for log in audit_logs),
    "failed_operations": [log for log in audit_logs if log['status'] == 'error'],
    "governance_violations": [log for log in audit_logs if not log['governance_passed']]
}
```

---

### Example 4: Custom MCP Server Integration

```python
# Connect to custom MCP server (e.g., internal analytics tools)
agent = MCPAgent(
    llm="ollama",
    mcp_servers=[
        "file_system",                           # Built-in
        "web_search",                            # Built-in
        {                                        # Custom
            "url": "stdio://internal-analytics-server",
            "args": ["--config", "/etc/analytics.json"],
            "timeout": 30
        }
    ]
)

# Now has tools from all servers:
# - file_system.read_file, write_file, etc.
# - web_search.search, scrape, etc.
# - analytics.generate_report, forecast_sales, etc.

response = await agent.chat("""
    1. Query the database for Q4 sales
    2. Search for competitor benchmarks
    3. Generate a competitive analysis report
""")

# Agent intelligently uses all 3 MCP servers
print(f"Servers used: {response['mcp_servers_used']}")
# Output: ['database_client', 'web_search', 'internal-analytics-server']
```

---

### Example 5: Production Configuration with Strict Governance

```python
def strict_approval(tool_name, args):
    """Only allow read operations, deny writes/deletes."""
    server, tool = tool_name.split(".")
    
    # Only allow safe read operations
    if tool in ["read_file", "search", "query"]:
        return True
    
    # Deny everything else
    return False

agent = MCPAgent(
    llm="azure",  # Enterprise provider
    model="gpt-4",
    mcp_servers=[
        {"url": "stdio://enterprise-tools", "args": ["--prod"]},
        "file_system"
    ],
    debug=False,                    # Quiet mode
    max_iterations=5,               # Prevent runaway
    temperature=0.2,                # Deterministic
    memory=True,                    # Persistent knowledge
    tool_governance_enabled=True,   # Strict compliance
    audit_logs_path="/var/log/prod_audit.log"
)

# Set strict approval
agent.set_callbacks(on_tool_approval=strict_approval)

# All executions logged, limited to read operations, deterministic
response = await agent.chat("Analyze Q4 data and competitor trends")
```

---

## Loading MCP Servers

```python
agent = MCPAgent(llm="ollama")

# Load built-in servers
agent.load_mcp_server("file_system")
agent.load_mcp_server("web_search")
agent.load_mcp_server("database_client")

# Or load custom server
agent.load_mcp_server({
    "url": "http://internal-api:8080/mcp",
    "timeout": 30,
    "args": ["--mode", "production"]
})

# List available tools
tools = agent.get_available_tools()
for server, server_tools in tools.items():
    print(f"{server}:")
    for tool in server_tools:
        print(f"  - {tool['name']}: {tool['description']}")
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **"MCP server connection failed"** | Check server is running, verify URL and port |
| **Tool not appearing** | Run `get_available_tools()` to debug, check server startup args |
| **Approval callbacks not firing** | Ensure `auto_approve=False`; verify callback signature |
| **Slow execution** | Reduce `mcp_timeout_seconds`; load only needed servers |
| **Audit logs missing** | Set `tool_governance_enabled=True`; verify `audit_logs_path` write access |

---

## Next Steps

- **[Build Custom MCP Servers](https://modelcontextprotocol.io)** — MCP documentation
- **[Agent (Full)](../agents/full-agent)** — Simpler version with Python tools only
- **[SmartAgent](../agents/smart-agent)** — Project-aware with built-in tools
- **[Compare Agents](../agents/agents-overview)** — Full feature matrix
