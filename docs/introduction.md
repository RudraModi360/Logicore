---
title: Introduction
description: Logicore is a powerful Python framework designed for building intelligent, multi-provider AI agents.
---

Logicore is a production-ready Python framework engineered for building intelligent AI agents capable of natively executing tools, retaining conversational memory, and managing automated workflows independently of the provider backend.

## Why Logicore?

Current frameworks either lock you into a single ecosystem (e.g., OpenAI) or bury tool execution in complex abstractions. Logicore guarantees your application-layer logic remains identical whether you use local execution (`Ollama`) or cloud APIs (`Gemini`, `OpenAI`).

---

## Core Component Schemas

To understand Logicore, developers should be familiar with its primary entity schemas.

### 1. The `Agent` Class
The `Agent` orchestrates conversational flow and manages bidirectional tool execution.

**Initialization Schema:**

<ParamField path="llm" type="BaseProvider" required>
  The initialized LLM provider instance.
</ParamField>

<ParamField path="role" type="str" required>
  The name of the agent (e.g. "Math Assistant").
</ParamField>

<ParamField path="system_message" type="str">
  System prompt instructing the agent on its behavior constraints.
</ParamField>

<ParamField path="tools" type="List[Callable]">
  Array of standard Python functions to expose to the LLM.
</ParamField>

<ParamField path="memory" type="BaseMemory">
  Optional memory module for long-term state persistence.
</ParamField>

<ParamField path="max_iterations" type="int">
  Prevents infinite loops. Hard cap on tool chains per user query. Default: `5`
</ParamField>

<ParamField path="debug" type="bool">
  Enables verbose terminal logging. Default: `False`
</ParamField>

**Execution (`chat()`) Response Schema:**
The `await agent.chat(...)` command returns a dictionary containing the final synthesized state of the conversation layer.
```json
{
  "role": "assistant",
  "content": "The execution result formatted as a final user-facing response.",
  "tool_calls": null
}
```

### 2. The Base `Provider` Interface
Providers normalize inputs and handle streaming token extraction natively.

**Standard Initialization:**

<ParamField path="model_name" type="str" required>
  The target model identifier (e.g., `qwen3.5:0.8b`, `gemini-1.5-flash`).
</ParamField>

<ParamField path="temperature" type="float">
  Adjusts creativity of responses. Default: `0.7`.
</ParamField>

### 3. Automatic Tool Schemas
Logicore dynamically converts standard Python TypeHints into JSON capability schemas. 

**Python Definition:**
```python
def check_server(ip_address: str, retries: int = 3, **kwargs) -> dict:
    """
    Pings a server to check its availability.
    
    Args:
        ip_address (str): The target IPv4 address.
        retries (int): Number of ping attempts.
    """
    return {"status": "online"}
```

**Underlying Parsed JSON Schema (Sent to LLM):**
```json
{
  "type": "function",
  "function": {
    "name": "check_server",
    "description": "Pings a server to check its availability.",
    "parameters": {
      "type": "object",
      "properties": {
        "ip_address": {
          "type": "string",
          "description": "The target IPv4 address."
        },
        "retries": {
          "type": "integer",
          "description": "Number of ping attempts.",
          "default": 3
        }
      },
      "required": ["ip_address"]
    }
  }
}
```
