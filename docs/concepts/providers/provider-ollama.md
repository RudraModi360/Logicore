---
title: Ollama Provider
description: Use local Ollama models directly with Logicore Agent.
---

# Ollama Provider

Use `OllamaProvider` when you want local inference, offline usage, or full control over your runtime.

## Install

```bash
pip install logicore ollama
```

Make sure the Ollama daemon is running and the model is available:

```bash
ollama pull qwen3.5:0.8b
```

## Use directly with Agent

```python
import asyncio
from logicore.agents.agent import Agent
from logicore.providers.ollama_provider import OllamaProvider

async def main():
    provider = OllamaProvider(model_name="qwen3.5:0.8b")

    agent = Agent(
        llm=provider,
        role="Local Assistant",
        system_message="Be concise and accurate."
    )

    result = await agent.chat("Summarize why local models are useful.")
    print(result["content"])

asyncio.run(main())
```

## With tools

```python
def get_weather(city: str) -> str:
    """Get weather information for a city."""
    return f"Weather in {city}: 27°C, clear"

agent = Agent(llm=provider, tools=[get_weather])
```

## Multimodal input support

`OllamaProvider` accepts mixed content lists (text + image). Use a vision model such as `qwen3-vl:*`, `llava:*`, or other vision-capable Ollama models.

```python
agent = Agent(
    llm=OllamaProvider(model_name="qwen3-vl:latest"),
    role="Vision Assistant"
)

message = [
    {"type": "text", "text": "Describe this image in one sentence."},
    {"type": "image_url", "image_url": r"path"}
]

result = await agent.chat(message)
print(result["content"])
```

Supported `image_url` values:
- Local file path (Windows/Linux/macOS)
- `https://...` image URL
- `data:image/...;base64,...`

## Notes

- Best for privacy-sensitive and offline workloads.
- Vision requests need a vision-capable model.
- Vision + tools in the same turn may be limited for some Ollama models.
- If a model is missing locally, call `provider.pull_model()`.
