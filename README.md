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
pip install agentry_community
```

### Basic Usage

```python
import asyncio
from agentry import Agent

async def main():
    # Create an agent with Ollama
    agent = Agent(llm="ollama", model="gpt-oss:20b:cloud")
    agent.load_default_tools()
    
    response = await agent.chat("What files are in the current directory?")
    print(response)

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

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Contact

- **GitHub**: [RudraModi360/Agentry](https://github.com/RudraModi360/Agentry)
- **Email**: rudramodi9560@gmail.com
