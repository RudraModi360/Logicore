---
title: Overview
description: Overview of tool workflows, built-in tools, and custom tool patterns.
---

Tools are executable capabilities that an agent can call during reasoning. In Logicore, a tool is exposed to the model as a JSON schema and executed as Python.

## Where tool code lives

Core tool implementations are in `logicore/tools/`:
- `base.py` defines `BaseTool` and `ToolResult`
- `registry.py` registers default built-in tools
- `filesystem.py`, `execution.py`, `web.py`, `git.py`, `document.py`, `convert_document.py`, `office_tools.py`, `pdf_tools.py`, `media_search.py`, `cron_tools.py`
- `agent_tools.py` defines Smart Agent-specific tools (`datetime`, `notes`, `memory`, `bash`, `think`)

## Recommended Reading Order

- [How Tools Work Internally](./tools-overview.md) — schema generation, approval, execution flow.
- [Ways of Making Tools](./tools-ways.md) — all implementation paths with examples.
- [Built-in Tools](./tools-built-in.md) — what ships by default and how to use it in agents.
- [Custom Tools](./tools-custom.md) — function-based and schema-first custom tool authoring.

## Quick Start

### 1) Use built-in tools
```python
from logicore import Agent

agent = Agent(llm="ollama", tools=True)
result = await agent.chat("Read README.md and summarize setup steps")
```

### 2) Add custom function tools
```python
from logicore import Agent

def get_weather(city: str, unit: str = "c") -> str:
    """Get current weather for a city.

    Args:
        city (str): City name, e.g. "Bangalore".
        unit (str): Temperature unit: "c" or "f".
    """
    return f"{city}: 27°{unit.upper()}"

agent = Agent(llm="ollama")
agent.register_tool_from_function(get_weather)
```

## Tool vs Skill

- **Tool:** one action (e.g., `read_file`, `web_search`, `add_cron_job`).
- **Skill:** a reusable domain package of instructions + optional tools.
