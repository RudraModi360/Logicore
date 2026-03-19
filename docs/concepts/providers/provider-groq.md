---
title: Groq Provider
description: Use Groq models directly with Logicore Agent.
---

# Groq Provider

Use `GroqProvider` when low latency and cost efficiency are priorities.

## Install

```bash
pip install logicore groq
```

Set your API key:

```bash
set GROQ_API_KEY=your_key_here
```

## Use directly with Agent

```python
import asyncio
from logicore.agents.agent import Agent
from logicore.providers.groq_provider import GroqProvider

async def main():
    provider = GroqProvider(
        model_name="llama-3.3-70b-versatile"
    )

    agent = Agent(
        llm=provider,
        role="Fast Assistant",
        system_message="Return clear, fast responses."
    )

    result = await agent.chat("Give 3 tips to speed up API responses.")
    print(result["content"])

asyncio.run(main())
```

## With tools

```python
def lookup_stock(symbol: str) -> str:
    """Return latest mocked stock price."""
    return f"{symbol}: 120.5 USD"

agent = Agent(llm=provider, tools=[lookup_stock])
```

## Multimodal input support

`GroqProvider` supports text + image input when you choose a vision-capable Groq model (for example `meta-llama/llama-4-scout-17b-16e-instruct`).

```python
agent = Agent(
    llm=GroqProvider(model_name="meta-llama/llama-4-scout-17b-16e-instruct"),
    role="Vision Assistant"
)

message = [
    {"type": "text", "text": "What do you see in this image?"},
    {"type": "image_url", "image_url": r"path"}
]

result = await agent.chat(message)
print(result["content"])
```

Supported `image_url` values:
- Local file path (auto-converted to base64)
- `https://...` image URL
- `data:image/...;base64,...`

## Notes

- Local image paths are converted to base64 automatically.
- Use a vision-capable Groq model for image inputs.
- If you see an empty-response error, switch models and retry.
