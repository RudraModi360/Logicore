---
title: Quickstart
description: Build your first Logicore agent in 5 minutes.
---

Get a working AI agent up and running in minutes. This guide takes you from zero to a fully functional agent with tools.

---

## Step 1: Install Logicore

```bash
pip install logicore
```

---

## Step 2: Create Your First Agent

Start with a basic agent that can chat without tools:

```python
from logicore.agents.agent import Agent
import asyncio

async def main():
    agent = Agent(llm="ollama")  # Uses Ollama or specify: "openai", "gemini", "groq", "azure"
    
    response = await agent.chat("What is an AI agent?")
    print(response['content'])

asyncio.run(main())
```

Run it:
```bash
python your_script.py
```

That's it! You have a working agent.

---

## Step 3: Add a Custom Tool

Tools are simple Python functions. Logicore auto-converts them to LLM-callable tools:

```python
def check_weather(location: str, unit: str = "fahrenheit", **kwargs) -> dict:
    """
    Fetches the current weather for a specific location.
    
    Args:
        location (str): The city or zip code to look up.
        unit (str): The temperature unit. Options: 'fahrenheit', 'celsius'.
    
    Returns:
        dict: Weather information with temperature and conditions.
    """
    if "seattle" in location.lower():
        return {"temperature": 72, "conditions": "sunny", "unit": unit}
    return {"temperature": 65, "conditions": "cloudy", "unit": unit}
```

---

## Step 4: Register the Tool and Run

```python
from logicore.agents.agent import Agent
import asyncio

def check_weather(location: str, unit: str = "fahrenheit", **kwargs) -> dict:
    """Fetches the current weather for a specific location."""
    if "seattle" in location.lower():
        return {"temperature": 72, "conditions": "sunny", "unit": unit}
    return {"temperature": 65, "conditions": "cloudy", "unit": unit}

async def main():
    agent = Agent(
        llm="ollama",
        tools=[check_weather]  # Register your tool
    )
    
    response = await agent.chat("What's the weather in Seattle?")
    print(response['content'])

asyncio.run(main())
```

Your agent now:
- Receives the user question
- Decides whether to use `check_weather`
- Executes the tool automatically
- Synthesizes the final answer

---

## Step 5: Enable Real-Time Feedback (Streaming)

See tokens as they arrive:

```python
async def main():
    agent = Agent(llm="ollama", tools=[check_weather])
    
    def on_token(token):
        print(token, end="", flush=True)
    
    response = await agent.chat(
        "What's the weather in Seattle?",
        callbacks={"on_token": on_token},
        stream=True
    )
    print("\nFinal:", response['content'])

asyncio.run(main())
```

---

## Step 6: Control Tool Approval

By default, tools require approval. Enable auto-approval for safe tools:

```python
agent = Agent(llm="ollama", tools=[check_weather])
agent.set_auto_approve_all(True)  # All tools execute without approval

response = await agent.chat("What's the weather in Seattle?")
```

Or use custom approval logic:

```python
async def approve_tool(session_id, tool_name, args):
    if tool_name == "delete_file":
        return False  # Deny dangerous operations
    return True  # Auto-approve everything else

agent.set_callbacks(on_tool_approval=approve_tool)
```

---

## Complete Working Example

```python
from logicore.agents.agent import Agent
import asyncio

def check_weather(location: str, unit: str = "fahrenheit", **kwargs) -> dict:
    """Fetches the current weather for a specific location."""
    if "seattle" in location.lower():
        return {"temperature": 72, "conditions": "sunny", "unit": unit}
    return {"temperature": 65, "conditions": "cloudy", "unit": unit}

async def main():
    # Create agent with streaming, tools, and auto-approval
    agent = Agent(
        llm="ollama",
        tools=[check_weather],
        role="Weather Assistant",
        system_message="Use the check_weather tool to answer weather questions."
    )
    agent.set_auto_approve_all(True)
    
    # Enable streaming with token callback
    def on_token(token):
        print(token, end="", flush=True)
    
    response = await agent.chat(
        "What's the weather in Seattle?",
        callbacks={"on_token": on_token},
        stream=True
    )
    
    print("\n\nFinal response:", response['content'])

asyncio.run(main())
```

---

## Next Steps

- **[Explore Concepts](./concepts/agents)** — Understand agents, skills, memory
- **[Check API Reference](./concepts/)** — Deep dive into all classes and methods
- **[Load Skills](./concepts/skills)** — Pre-built capability packs (web_research, code_review, etc.)

---

## How It Works Under the Hood

### Tool Schema Conversion

When you register a function, Logicore automatically converts your Python function into an LLM-callable JSON schema:

**Your Python Function:**
```python
def check_weather(location: str, unit: str = "fahrenheit", **kwargs) -> dict:
    """Fetches the current weather for a specific location."""
    ...
```

**Generated JSON Schema (sent to LLM):**
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

Features:
- Parses type hints → JSON Schema types
- Extracts docstrings → descriptions
- Handles defaults → optional parameters
- `**kwargs` absorbs hallucinated parameters (safer with local models)

### Execution Flow

**1. LLM Decides to Use Your Tool:**
The LLM returns a structured tool call request:
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

**2. Logicore Executes the Function:**
- Parses the `arguments` JSON
- Maps to your Python function
- Executes: `check_weather(location="Seattle")`
- Gets result: `{"temperature": 72, "conditions": "sunny", "unit": "fahrenheit"}`
- Formats as a tool message for the LLM

**3. LLM Synthesizes the Answer:**
The LLM receives the tool result, analyzes it, and returns the final answer:
```json
{
  "role": "assistant",
  "content": "The weather in Seattle today is 72°F and sunny."
}
```

### Multi-Turn Conversation

Agents handle multi-turn conversations automatically:

```python
agent = Agent(llm="ollama", tools=[check_weather])

# Turn 1
response1 = await agent.chat("What's the weather in Seattle?")

# Turn 2 - Agent remembers previous context
response2 = await agent.chat("How about New York?")

# Turn 3
response3 = await agent.chat("Which city is warmer?")
# Agent compares both results automatically
```

Each turn is added to the conversation history, enabling long-running sessions with full context.

---

## Troubleshooting

### Agent Not Using Tools
- Ensure tools are registered: `tools=[check_weather]`
- Check tool docstrings are present (required for descriptions)
- Enable debug mode: `Agent(..., debug=True)`

### Hallucinated Parameters
- Add `**kwargs` to all tool functions
- Local models (Ollama) hallucinate more; cloud models are safer

### Tool Execution Hangs
- Check your tool implementation doesn't block (use async where needed)
- Set `max_iterations=5` to prevent infinite loops

### Provider Connection Issues
- **Ollama**: Ensure running locally (`ollama serve`)
- **OpenAI**: Set `OPENAI_API_KEY` environment variable
- **Azure**: Set `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY`

