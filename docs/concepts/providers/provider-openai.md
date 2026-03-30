---
title: OpenAI Provider
description: Use OpenAI API models directly with Logicore Agent.
---

# OpenAI Provider

Use `OpenAIProvider` when you want direct OpenAI API access with strong tool calling support.

## Install

```bash
pip install logicore openai
```

Set API key:

```bash
set OPENAI_API_KEY=your_key_here
```

## Use directly with Agent

```python
import asyncio
from logicore.agents.agent import Agent
from logicore.providers.openai_provider import OpenAIProvider

async def main():
    provider = OpenAIProvider(model_name="gpt-4o-mini")

    agent = Agent(
        llm=provider,
        role="Cloud Assistant",
        system_message="Provide concise and reliable answers."
    )

    result = await agent.chat("Explain event-driven architecture in simple terms.")
    print(result["content"])

asyncio.run(main())
```

## With tools

```python
def calculate_tax(amount: float, rate: float) -> str:
    """Calculate tax amount from value and rate."""
    return str(amount * rate)

agent = Agent(llm=provider, tools=[calculate_tax])
```

## Multimodal input support

`OpenAIProvider` supports multimodal prompts with vision-capable models (for example `gpt-4o`, `gpt-4o-mini`).

```python
agent = Agent(
    llm=OpenAIProvider(model_name="gpt-4o-mini"),
    role="Vision Assistant"
)

message = [
    {"type": "text", "text": "What is shown in this image?"},
    {"type": "image_url", "image_url": r"path"}
]

result = await agent.chat(message)
print(result["content"])
```

Supported `image_url` values:
- Local file path
- `https://...` image URL
- `data:image/...;base64,...`

## Notes

- Supports non-streaming and streaming chat paths.
- If model returns empty output, Logicore raises a descriptive error.
- Use model names available in your OpenAI account/project.
