---
title: Quickstart
description: A comprehensive developer guide to initializing Logicore agents with complete tool and response schemas.
---

This guide walks you through creating a fully functional, tool-enabled AI agent. We will cover the exact Python implementations alongside their underlying JSON execution schemas.

## 1. Defining a Robust Custom Tool

Tools in Logicore are standard Python functions. Logicore parses your type hints and docstrings into formal JSON schemas for the LLM. 

**Best Practice:** Always include `**kwargs` to safely absorb hallucinated parameters (common in smaller local models).

**Python Implementation:**
```python
def check_weather(location: str, unit: str = "fahrenheit", **kwargs) -> dict:
    """
    Fetches the current weather for a specific location.
    
    Args:
        location (str): The city or zip code to look up.
        unit (str): The temperature unit. Options: 'fahrenheit', 'celsius'.
    """
    if "seattle" in location.lower():
        return {"temperature": 72, "conditions": "sunny", "unit": unit}
    return {"temperature": 65, "conditions": "cloudy", "unit": unit}
```

**Underlying Parsed Input Schema (Sent to LLM):**
```json
{
  "type": "function",
  "function": {
    "name": "check_weather",
    "description": "Fetches the current weather for a specific location.",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {"type": "string", "description": "The city or zip code to look up."},
        "unit": {"type": "string", "description": "The temperature unit. Options: 'fahrenheit', 'celsius'.", "default": "fahrenheit"}
      },
      "required": ["location"]
    }
  }
}
```

---

## 2. Initializing the Provider & Agent

The `Agent` class orchestrates the interaction loop, managing `tool_call` requests, execution, and semantic state.

**Python Initialization:**
```python
from logicore.providers.ollama_provider import OllamaProvider
from logicore.agents.agent import Agent

# Initialize a streaming-capable local provider
provider = OllamaProvider(model_name="qwen3.5:0.8b")

agent = Agent(
    llm=provider,
    role="Weather Assistant",
    system_message="Use the check_weather tool to answer questions. Do not hallucinate data.",
    tools=[check_weather],
    max_iterations=5,
    debug=True
)
```

---

## 3. The Execution Loop (End-to-End Trace)

Logicore handles the multi-turn execution loop automatically. 

**Triggering the Execution:**
```python
import asyncio

async def run():
    # Progress streaming callback for real-time <think> tokens
    def on_token(token):
        print(token, end="", flush=True)

    response = await agent.chat(
        "What's the weather like in Seattle today?", 
        callbacks={"on_token": on_token},
        stream=True
    )
    print("\nFinal Result:", response['content'])

asyncio.run(run())
```

### Trace: Step 1 - The LLM Decides to Use a Tool
The LLM evaluates the prompt and returns a structured tool call. Logicore intercepts this.
**LLM Response Schema:**
```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "check_weather",
        "arguments": "{\"location\": \"Seattle\"}"
      }
    }
  ]
}
```

### Trace: Step 2 - Logicore Executes the Function
Logicore parses the `arguments` JSON, maps it to your `check_weather(location="Seattle")` Python function, executes it, and formats the result into a `tool` role message for the LLM.

**Context Update appended to LLM Memory:**
```json
{
  "role": "tool",
  "content": "{\"temperature\": 72, \"conditions\": \"sunny\", \"unit\": \"fahrenheit\"}",
  "tool_call_id": "call_abc123",
  "name": "check_weather"
}
```

### Trace: Step 3 - Final Output Synthesis
The LLM receives the tool result, analyzes the data, and returns the final synthesized string to the user.
**Final Return Schema from `agent.chat()`:**
```json
{
  "role": "assistant",
  "content": "The weather in Seattle today is 72°F and sunny.",
  "tool_calls": null
}
```
