---
title: Ways of Making Tools
description: Different patterns to create tools, from simple function registration to schema-first control.
---

Use the approach that matches your control requirements.

## 1) Function-first (fastest)

Best when you want minimal code and rely on Logicore to generate schema from type hints + docstrings.

```python
from logicore import Agent

def summarize_text(text: str, max_points: int = 5, **kwargs) -> str:
    """Summarize text into concise bullet points.

    Args:
        text (str): The source text to summarize.
        max_points (int): Maximum bullet points in the summary.
    """
    return f"Summarized into {max_points} points"

agent = Agent(llm="ollama")
agent.register_tool_from_function(summarize_text)
```

Why use it:
- Lowest setup cost.
- Works well for internal business helpers.
- Auto schema generation from Python signature.

---

## 2) Pass functions during agent initialization

Best when your tool set is known at startup.

```python
from logicore import Agent

def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b

agent = Agent(
    llm="ollama",
    tools=[add, multiply]
)
```

Why use it:
- Clean startup config.
- Good for fixed tool bundles.

---

## 3) Schema-first with custom executor

Best when you need full control over tool contract, naming, and compatibility.

```python
from logicore import Agent

def run_sql(query: str, limit: int = 100):
    if not query.strip().lower().startswith("select"):
        return {"success": False, "error": "Only SELECT statements are allowed"}
    return {"success": True, "rows": [{"id": 1, "name": "Ada"}], "limit": limit}

schema = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Execute read-only SQL on analytics DB.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Read-only SQL query"},
                "limit": {"type": "integer", "description": "Row cap", "default": 100}
            },
            "required": ["query"]
        }
    }
}

agent = Agent(llm="ollama")
agent.add_custom_tool(schema, run_sql)
```

Why use it:
- Exact response contract and naming.
- Helpful when integrating with strict external APIs.

---

## 4) Class-based tool (`BaseTool`) for reusable modules

Best when you are building reusable internal tooling with validation models and shared logic.

```python
from pydantic import BaseModel, Field
from logicore.tools.base import BaseTool, ToolResult
from logicore import Agent


class InvoiceLookupParams(BaseModel):
    invoice_id: str = Field(..., description="Invoice identifier")


class InvoiceLookupTool(BaseTool):
    name = "lookup_invoice"
    description = "Fetch invoice status by ID"
    args_schema = InvoiceLookupParams

    def run(self, invoice_id: str) -> ToolResult:
        return ToolResult(success=True, content={"invoice_id": invoice_id, "status": "paid"})


tool = InvoiceLookupTool()
agent = Agent(llm="ollama")
agent.add_custom_tool(tool.schema, lambda **kwargs: tool.run(**kwargs))
```

Why use it:
- Strong typing via `args_schema`.
- Easy to reuse and test.
- Matches Logicore built-in tool architecture.

---

## Choosing the Right Pattern

| Scenario | Recommended pattern |
| --- | --- |
| Fast prototyping | Function-first |
| Fixed startup toolset | `tools=[func1, func2]` |
| Strict API/tool contract | Schema-first + executor |
| Reusable internal toolkit | `BaseTool` class |

## Next Steps

- [How Tools Work Internally](./tools-overview.md)
- [Built-in Tools](./tools-built-in.md)
- [Custom Tools](./tools-custom.md)
