---
title: Gemini Provider
description: Use Google Gemini models directly with Logicore Agent.
---

# Gemini Provider

Use `GeminiProvider` for strong multimodal support and Google-hosted inference.

## Install

```bash
pip install logicore google-genai
```

Set one of these API keys:

```bash
set GEMINI_API_KEY=your_key_here
```

or

```bash
set GOOGLE_API_KEY=your_key_here
```

## Use directly with Agent

```python
import asyncio
from logicore.agents.agent import Agent
from logicore.providers.gemini_provider import GeminiProvider

async def main():
    provider = GeminiProvider(model_name="gemini-1.5-flash")

    agent = Agent(
        llm=provider,
        role="Multimodal Assistant",
        system_message="Answer accurately and briefly."
    )

    result = await agent.chat("Explain retrieval augmented generation in 4 lines.")
    print(result["content"])

asyncio.run(main())
```

## With tools

```python
def fetch_doc_title(url: str) -> str:
    """Fetch title for a URL."""
    return f"Title for {url}"

agent = Agent(llm=provider, tools=[fetch_doc_title])
```

## Multimodal input support

`GeminiProvider` natively supports multimodal prompts. Pass a list with text and image parts.

```python
agent = Agent(
    llm=GeminiProvider(model_name="gemini-2.5-pro"),
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
- Local file path
- `https://...` image URL
- `data:image/...;base64,...`

## Notes

- System prompts are mapped to Gemini system instructions.
- Tool calls are converted to Gemini function declarations.
- For multimodal prompts, use models that support the requested media type.
