---
title: SmartAgent
description: Project-aware agent with built-in tools, dual modes, and intelligent learning.
---

# SmartAgent

**SmartAgent** is the most versatile agent type. It combines Agent's power with 9 pre-built essential tools (web search, bash, memory, scheduling), dual modes (solo for exploration, project for focused work), and automatic learning capture.

---

## When to Use SmartAgent

- Building project-specific AI assistants
- Switching between focused work and exploratory chat
- Leveraging built-in web search, bash, and scheduling
- Capturing learnings and insights automatically
- Multi-project management with context switching

---

## Quick Start: Solo Mode

```python
from logicore.agents.agent_smart import SmartAgent
import asyncio

async def main():
    # General chat with web search, memory, bash
    agent = SmartAgent(llm="ollama", mode="solo")
    
    response = await agent.chat("What are latest AI trends in 2024?")
    print(response['content'])

asyncio.run(main())
```

---

## Quick Start: Project Mode

```python
agent = SmartAgent(llm="ollama", mode="project")

# Create and switch to project
agent.create_project(
    project_id="ml-pipeline",
    title="ML Pipeline Builder",
    goal="Build and optimize data processing",
    environment={"FRAMEWORK": "pytorch", "PYTHON": "3.11"}
)
agent.switch_to_project("ml-pipeline")

# Responses now consider project context
response = await agent.chat(
    "What's the best approach for handling missing data?"
)
print(response['content'])
```

---

## How It Works

SmartAgent operates in two modes:

**Solo Mode**: Unrestricted reasoning with web search, bash, memory, and notes. Perfect for learning and exploration.

**Project Mode**: Project-aware reasoning that injects project context into every response. Automatically captures significant insights and stores them in project-specific memory. Perfect for focused development work.

Both modes include 9 built-in tools automatically loaded (no configuration needed):

| Tool | Purpose |
|------|---------|
| `web_search` | Search internet and fetch pages |
| `image_search` | Search for images |
| `bash_execute` | Run shell commands safely |
| `memory_store` | Store/retrieve semantic memories |
| `capture_note` | Create timestamped notes |
| `datetime` | Get time and schedule jobs |
| `project_memory` | Project-specific memory (project mode) |
| `switch_project` | Switch projects (project mode) |
| `create_project` | Create new projects (project mode) |

---

## Configuration Parameters

### Constructor Parameters

```python
agent = SmartAgent(
    llm: str = "ollama",                    # ✓ Required: LLM provider
    model: str = None,                      # Specific model
    mode: str = "solo",                     # "solo" or "project"
    tools: List[Callable] = None,           # Additional custom tools
    memory: bool = True,                    # Enable persistent memory
    memory_type: str = "intelligent",       # "intelligent" or "default"
    debug: bool = False,                    # Enable logging
    temperature: float = 0.7,               # LLM randomness
    capture_learnings: bool = True,         # Auto-capture insights
    workspace_root: str = None,             # Root for bash operations
    **kwargs
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | str | Required | Provider: `ollama`, `openai`, `gemini`, `groq`, `azure` |
| `model` | str | Provider default | Model name |
| `mode` | str | "solo" | "solo" (exploration) or "project" (focused) |
| `tools` | List | None | Custom tools to add to built-ins |
| `memory` | bool | True | Enable persistent memory |
| `memory_type` | str | "intelligent" | "intelligent" (context-aware) |
| `debug` | bool | False | Print execution details |
| `temperature` | float | 0.7 | Randomness (0-1) |
| `capture_learnings` | bool | True | Auto-capture insights (project mode) |
| `workspace_root` | str | None | Root for bash/file operations |

---

## Chat Method: Input & Output

### Request Parameters

```python
response = await agent.chat(
    message: str,                           # ✓ Required: Your prompt
    callbacks: Dict = None,                 # Optional: `{"on_token": fn}`
    stream: bool = False,                   # Optional: Enable streaming
    capture_as_learning: bool = False,      # Optional: Force capture as learning
    learning_category: str = None,          # Optional: Learning type (project mode)
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
    "tool_calls": List[Dict | None],        # Tools executed (web_search, bash, etc.)
    "tokens_used": int,                     # Total tokens
    "provider": str,                        # Provider used
    "model": str,                           # Model name
    "finish_reason": str,                   # "stop" or "max_tokens"
    "learning_captured": bool,              # Was learning captured? (project mode)
    "learning_id": str | None,              # Learning ID if captured
    "project_context": str | None,          # Active project (project mode only)
    "memory_updated": bool,                 # Was memory updated?
    "execution_steps": List[Dict],          # Tool execution history
    "metadata": dict                        # Timestamps, etc.
}
```

---

## Examples: Basic to Advanced

### Example 1: Solo Mode - Web Research

```python
agent = SmartAgent(llm="ollama", mode="solo")

# SmartAgent automatically uses web_search
response = await agent.chat(
    "What are the latest developments in LLMs in 2024?"
)

print(response['content'])
print(f"Tools used: {[tc['name'] for tc in response['tool_calls']]}")
# Output includes actual search results, not just LLM knowledge
```

**Output:**
```
Based on recent searches, the latest LLM developments in 2024 include:
1. Multimodal models combining text and vision...
2. More efficient fine-tuning methods...
[Details from web search results]

Tools used: ['web_search']
```

---

### Example 2: Project Mode - Context-Aware Development

```python
agent = SmartAgent(llm="openai", mode="project", memory=True)

# Create a project
agent.create_project(
    project_id="ecommerce-api",
    title="E-Commerce API",
    goal="Build scalable REST API for products and orders",
    environment={"FRAMEWORK": "FastAPI", "DB": "PostgreSQL", "CACHE": "Redis"},
    key_files=["src/", "tests/", "requirements.txt"]
)

# Switch to project
agent.switch_to_project("ecommerce-api")

# All responses consider project context
response = await agent.chat(
    "What's the best way to structure the product database schema?"
)

# Response considers project structure, goals, environment
print(response['content'])
print(f"Project context: {response['project_context'][:100]}...")
```

**Output:**
```
Based on your e-commerce API project goals, I'd recommend:
1. Products table with: id, name, sku, price, inventory...
2. Orders table with: id, user_id, products, status, timestamp...
[Recommendations tailored to your FastAPI + PostgreSQL stack]

Project context: E-Commerce API - Build scalable REST API...
```

---

### Example 3: Learning Capture & Retrieval

```python
agent = SmartAgent(llm="ollama", mode="project", memory=True)

agent.create_project("backend", "Node.js Backend")
agent.switch_to_project("backend")

# Conversation 1: Capture learning
response = await agent.chat(
    """What approach should we use for API rate limiting?
    We need to handle 10k requests/min and prevent abuse.""",
    capture_as_learning=True,
    learning_category="performance"
)

# Conversation 2: Retrieval (agent automatically recalls)
response = await agent.chat(
    "How should we implement rate limiting for our API?"
)

# Agent: "Based on our earlier discussion about rate limiting..."
print(response['content'])
```

**Output:**
```
Based on our earlier discussion about rate limiting and your 10k req/min requirement,
we recommended token bucket algorithm with Redis. This approach offers:
- Distributed rate limiting across multiple instances
- O(1) complexity...
[Automatically references previous learning]
```

---

### Example 4: Multi-Project Switching

```python
agent = SmartAgent(llm="ollama")

# Create 3 projects
for proj_id, title, goal in [
    ("web-app", "Frontend App", "React dashboard"),
    ("backend", "API", "FastAPI service"),
    ("data", "Pipeline", "ETL processing")
]:
    agent.create_project(proj_id, title, goal=goal)

# Switch and work on each
for proj_id in ["web-app", "backend", "data"]:
    agent.switch_to_project(proj_id)
    
    response = await agent.chat(
        f"What's the architecture for this {proj_id}?"
    )
    print(f"\n{proj_id.upper()}:")
    print(response['content'][:200])
```

**Output:**
```
WEB-APP:
For a React dashboard, recommended architecture includes components layer,
state management (Redux), API integration layer, and styling...

BACKEND:
FastAPI service architecture with route handlers, middleware, database models,
authentication layer, and error handling...

DATA:
ETL pipeline with data sources, transformation layer, validation, 
and destination storage...
```

---

## Building Custom Tools + Built-ins

```python
def analyze_performance(code: str) -> str:
    """Analyze code performance."""
    # Custom implementation
    return "Performance analysis..."

agent = SmartAgent(llm="ollama")
agent.register_tool_from_function(analyze_performance)

# Now has both built-in tools + custom tool
response = await agent.chat(
    "Analyze this code and search for best practices"
)
# Can use: web_search (built-in) + analyze_performance (custom)
```

---

## Project Management Methods

```python
# Create project
agent.create_project("project-id", "Title", goal="Goal")

# Switch to project
agent.switch_to_project("project-id")

# Switch to solo mode
agent.switch_to_solo()

# List all projects
projects = agent.list_projects()

# Get project context
context = agent.get_project_context_for_llm()
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **"Project not found"** | Check with `agent.list_projects()`, then create if missing |
| **Project context ignored** | Verify mode is "project": `print(agent.mode)` and you called `switch_to_project()` |
| **Web search not working** | Check internet connection and firewall rules |
| **Bash commands fail** | Set `workspace_root` to correct directory |
| **Learnings not captured** | Require `memory=True`, project mode, and significant response |

---

## Next Steps

- **[Agent (Full)](./full-agent)** — Lower-level control without built-in tools
- **[MCPAgent](../mcp/mcp-agent)** — Enterprise MCP tool integration
- **[BasicAgent](./basic-agent)** — Simplest chat without tools
- **[Compare Agents](./agents-overview)** — Full feature matrix
