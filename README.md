# Agentry

**A Modular AI Agent Framework for Python**

[![PyPI](https://img.shields.io/pypi/v/agentry-community)](https://pypi.org/project/agentry-community/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://rudramodi360-agentry.mintlify.app/)

Agentry is a powerful, privacy-focused AI agent framework designed for flexibility and ease of use. It provides a unified interface to interact with multiple LLM providers, comprehensive built-in tools, and MCP support.

## Documentation

**Full documentation is available at: [https://rudramodi360-agentry.mintlify.app/](https://rudramodi360-agentry.mintlify.app/)**

---

## Quick Start

### Installation

```bash
pip install logicore
```

### 2. Run your first Agent
Make sure you have [Ollama](https://ollama.com) installed and a model pulled (`ollama run qwen3.5:0.8b`).

```python
import asyncio
from logicore.agents.agent import Agent

# 1. Define a robust custom tool
def check_weather(location: str, **kwargs) -> str:
    """Checks the current weather for a specific location."""
    if "seattle" in location.lower():
        return "72°F and sunny."
    return "65°F and cloudy."

async def main():
    # 2. Initialize agent with Ollama by provider name
    agent = Agent(
        llm="ollama",
        role="Weather Assistant",
        system_message="Use the provided tools to answer user questions accurately.",
        tools=[check_weather],
        debug=True
    )
    
    # 3. Stream the execution live
    def on_token(token):
        print(token, end="", flush=True)

    print("Agent is thinking...\n")
    response = await agent.chat(
        "What's the weather like in Seattle today?", 
        callbacks={"on_token": on_token},
        stream=True
    )
    
    print("\n\nFinal Output:", response['content'])

if __name__ == "__main__":
    asyncio.run(main())
```

> **Jupyter/Colab Users:** Use `await agent.chat(...)` directly instead of `asyncio.run()`. See [full docs](https://rudramodi360-agentry.mintlify.app/getting-started#running-in-jupyter-notebook) for details.

### Launch CLI

```bash
agentry_cli
```

### Launch Web UI

```bash
agentry_gui
```

---

## Supported Providers

| Provider | Type | Models Tested |
|:---------|:-----|:--------------|
| **Ollama** | Local/Cloud | `gpt-oss:20b:cloud`, `glm-4.5:cloud`, `llama3.2` |
| **Groq** | Cloud | `llama-3.3-70b-versatile` |
| **Gemini** | Cloud | `gemini-2.0-flash` |
| **Azure** | Cloud | `claude-opus:4.5`, `gpt-4` |

---

## Features

- **Multi-Provider Support** - Ollama, Groq, Gemini, Azure OpenAI
- **Built-in Tools** - Filesystem, web search, code execution, documents
- **MCP Integration** - Connect external tool servers
- **Session Management** - Automatic persistence
- **Custom Tools** - Register any Python function

---

## Documentation Topics

For detailed information, visit the [full documentation](https://rudramodi360-agentry.mintlify.app/):

- [Getting Started](https://rudramodi360-agentry.mintlify.app/getting-started) - Installation guide
- [Core Concepts](https://rudramodi360-agentry.mintlify.app/core-concepts) - Architecture
- [API Reference](https://rudramodi360-agentry.mintlify.app/api-reference) - Complete API
- [Custom Tools](https://rudramodi360-agentry.mintlify.app/custom-tools) - Create tools
- [MCP Integration](https://rudramodi360-agentry.mintlify.app/mcp-integration) - External servers
- [Examples](https://rudramodi360-agentry.mintlify.app/examples) - Code samples
- [Troubleshooting](https://rudramodi360-agentry.mintlify.app/troubleshooting) - Common issues

---

## Contributing

Contributions are welcome! See [Contributing Guide](https://rudramodi360-agentry.mintlify.app/CONTRIBUTING).

---
*Built with ❤️ for multi-provider agentic workflows.*
