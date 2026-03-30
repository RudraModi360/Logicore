---
title: Custom Tools
description: Build custom tools with strong schemas, clear descriptions, and predictable outputs.
---

This guide focuses on building high-quality custom tools that LLMs can reliably call.

## Option A: Register a Python function

Logicore auto-generates the tool schema from the function signature and docstring.

```python
from logicore import Agent


def search_tickets(project: str, status: str = "open", limit: int = 20, **kwargs) -> dict:
    """Search project tickets.

    Args:
        project (str): Project key like CORE or API.
        status (str): Ticket status filter (open, closed, all).
        limit (int): Max rows to return.

    Returns:
        dict: Structured ticket results for the caller.
    """
    rows = [{"id": "CORE-1", "title": "Fix login", "status": status}]
    return {"project": project, "count": len(rows), "items": rows[:limit]}


agent = Agent(llm="ollama")
agent.register_tool_from_function(search_tickets)
```

---

## Option B: Define schema + executor manually

Use this when you need exact control over parameter schema and naming.

```python
from logicore import Agent


def get_exchange_rate(base: str, quote: str) -> dict:
    if base == quote:
        return {"success": True, "rate": 1.0}
    return {"success": True, "rate": 83.12, "base": base, "quote": quote}


schema = {
    "type": "function",
    "function": {
        "name": "get_exchange_rate",
        "description": "Get FX conversion rate for a currency pair.",
        "parameters": {
            "type": "object",
            "properties": {
                "base": {"type": "string", "description": "Base currency code, e.g. USD"},
                "quote": {"type": "string", "description": "Quote currency code, e.g. INR"}
            },
            "required": ["base", "quote"]
        }
    }
}


agent = Agent(llm="ollama")
agent.add_custom_tool(schema, get_exchange_rate)
```

---

## Guidelines for High-Quality Custom Tools

### 1) Strong types
- Add Python type hints to every parameter.
- Use concrete return types (`dict`, `str`, `list`) over ambiguous objects.
- For class-based tools, use a Pydantic `args_schema`.

### 2) Clear response schema
- Return JSON-serializable values only.
- Keep shape stable across success cases.
- Include explicit error fields for recoverable failures.

Recommended result shape:

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

### 3) Descriptions that help model routing
- Tool description should answer: *when should this tool be used?*
- Parameter descriptions should include accepted formats and examples.
- Docstrings should include `Args` and `Returns` sections.

### 4) Hallucination tolerance
- Include `**kwargs` in function-style tools to absorb unexpected parameters.
- Validate critical arguments in code and return friendly errors.

### 5) Deterministic behavior
- Keep tools single-purpose and predictable.
- Add timeouts/retries for external calls.
- Avoid side effects unless the tool is explicitly mutating state.

---

## Class-Based Custom Tool Example (`BaseTool`)

```python
from pydantic import BaseModel, Field
from logicore import Agent
from logicore.tools.base import BaseTool, ToolResult


class StockPriceParams(BaseModel):
    symbol: str = Field(..., description="Ticker symbol like MSFT")


class StockPriceTool(BaseTool):
    name = "get_stock_price"
    description = "Get latest stock price for a ticker symbol"
    args_schema = StockPriceParams

    def run(self, symbol: str) -> ToolResult:
        return ToolResult(success=True, content={"symbol": symbol, "price": 432.15})


tool = StockPriceTool()
agent = Agent(llm="ollama")
agent.add_custom_tool(tool.schema, lambda **kwargs: tool.run(**kwargs))
```

## Next Steps

- [Ways of Making Tools](./tools-ways.md)
- [Built-in Tools](./tools-built-in.md)
- [How Tools Work Internally](./tools-overview.md)
