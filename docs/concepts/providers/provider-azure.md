---
title: Azure Provider
description: Use Azure OpenAI, Azure AI Foundry, or Azure AI Inference deployments directly with Logicore Agent.
---

# Azure Provider

Use `AzureProvider` when you need enterprise Azure hosting, compliance controls, or Azure-based model deployments.

## Install

```bash
pip install logicore openai anthropic
```

Set environment variables:

```bash
set AZURE_API_KEY=your_key_here
set AZURE_ENDPOINT=https://your-resource.openai.azure.com
```

## Use directly with Agent

```python
import asyncio
from logicore.agents.agent import Agent
from logicore.providers.azure_provider import AzureProvider

async def main():
    provider = AzureProvider(
        model_name="gpt-4o-mini",      # Azure deployment name
        endpoint="https://your-resource.openai.azure.com",
        api_key="your_key_here",
        model_type="openai"            # openai | anthropic | inference
    )

    agent = Agent(
        llm=provider,
        role="Enterprise Assistant",
        system_message="Respond with concise, production-safe guidance."
    )

    result = await agent.chat("List 5 Azure governance best practices.")
    print(result["content"])

asyncio.run(main())
```

## Model type options

- `model_type="openai"`: Azure OpenAI deployments.
- `model_type="anthropic"`: Anthropic deployments via Azure AI Foundry.
- `model_type="inference"`: Azure AI Inference endpoints.

If omitted, Logicore auto-detects the model type from endpoint and deployment name.

## Multimodal input support

`AzureProvider` supports multimodal inputs when the target deployment supports vision (for example GPT-4o family on Azure OpenAI).

```python
agent = Agent(
    llm=AzureProvider(
        model_name="gpt-4o-mini",
        endpoint="https://your-resource.openai.azure.com",
        api_key="your_key_here",
        model_type="openai"
    )
)

message = [
    {"type": "text", "text": "Extract key details from this image."},
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

- `model_name` is your Azure deployment name, not always the raw model ID.
- For OpenAI-style deployments, default API version is applied automatically.
- For Anthropic on Azure, install `anthropic` and verify endpoint format.
- Vision support depends on your Azure deployment/model capabilities.
