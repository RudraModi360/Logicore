---
title: Installation
description: Install Logicore with your preferred provider and start building intelligent agents.
---

Getting Logicore running in your environment is simple. We recommend using a virtual environment.

## Requirements
* Python 3.10+
* An LLM provider (e.g., [Ollama](https://ollama.com) installed locally, or API keys for cloud providers)

## Installation Steps

### 1. Core Package
Install the base Logicore framework using your package manager:

```bash
pip install logicore
```

### 2. Provider-Specific Setup

#### For Local Models (Ollama)
If you want to run purely local agents (e.g., `qwen3.5`, `llama3`), ensure you have Ollama installed and running gracefully in the background.

```bash
# Pull your desired model before running the agent
ollama run qwen3.5:0.8b
```

#### For Cloud Models (Gemini/OpenAI)
Set your environment variables before initializing the provider:
```bash
export GEMINI_API_KEY="your-api-key"
export OPENAI_API_KEY="your-api-key"
```

## Verifying the Installation

To verify your installation, create a simple script `verify.py`:

```python
import asyncio
from logicore.providers.ollama_provider import OllamaProvider
from logicore.agents.agent import Agent

async def main():
    provider = OllamaProvider(model_name="qwen3.5:0.8b")
    agent = Agent(llm=provider, role="Greeter")
    response = await agent.chat("Say hello!")
    print(response['content'])

if __name__ == "__main__":
    asyncio.run(main())
```
