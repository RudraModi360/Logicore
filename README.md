<div align="center">

# 🧠 Logicore

**A modular, multi-provider AI agent framework for Python**

[![PyPI version](https://img.shields.io/pypi/v/logicore?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/logicore/)
[![Python](https://img.shields.io/pypi/pyversions/logicore?logo=python&logoColor=white)](https://pypi.org/project/logicore/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dm/logicore?color=orange)](https://pypi.org/project/logicore/)

Build intelligent, tool-using AI agents that work across **Gemini**, **Groq**, **Ollama**, **Azure OpenAI**, and **Anthropic** — with a single unified API.

[📖 Documentation](https://rudramodi360.github.io/Agentry/) · [🐛 Report Bug](https://github.com/RudraModi360/Agentry/issues) · [💡 Request Feature](https://github.com/RudraModi360/Agentry/issues)

</div>

---

## ✨ Why Logicore?

Logicore is a **lightweight, production-ready** Python framework for building AI agents that can use tools, remember context, and work with any major LLM provider. No vendor lock-in — swap providers with one line.

### 🔑 Key Features

| Feature | Description |
|---------|-------------|
|  **Multi-Provider** | Gemini, Groq, Ollama, Azure OpenAI, Anthropic — one interface |
|  **Tool Use** | Built-in file, web, git, PDF, Office, and code execution tools |
|  **MCP Support** | Model Context Protocol for dynamic tool discovery |
|  **Streaming** | Real-time token streaming with async callbacks |
|  **Vision** | Multimodal image understanding across supported models |
|  **Memory** | Session persistence and simple memory stores |
|  **Telemetry** | Built-in execution tracing and walkthrough generation |
|  **Skills** | Modular, reusable skill packs for domain-specific tasks |
|  **Hot Reload** | Live code reloading during development |

---

## 🚀 Quick Start

### Installation

```bash
# Core framework
pip install logicore

# With a specific provider
pip install logicore[gemini]    # Google Gemini
pip install logicore[groq]      # Groq
pip install logicore[ollama]    # Ollama (local)
pip install logicore[azure]     # Azure OpenAI / Anthropic

# Everything
pip install logicore[all]
```

### Your First Agent

```python
from logicore import BasicAgent, create_agent, tool

# Define a custom tool
@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"It's sunny and 24°C in {city}!"

# Create an agent with Groq
agent = create_agent(
    provider="groq",
    model="llama-3.3-70b-versatile",
    tools=[get_weather]
)

# Chat with your agent
response = await agent.chat("What's the weather in Tokyo?")
print(response)
```
---

### Multi-Provider Flexibility

```python
from logicore import Agent, SmartAgent
from logicore import GroqProvider, GeminiProvider, OllamaProvider

# Swap providers without changing your agent code
groq = GroqProvider(model_name="llama-3.3-70b-versatile")
gemini = GeminiProvider(model_name="gemini-2.0-flash")
ollama = OllamaProvider(model_name="llama3.2")

agent = Agent(provider=groq)  # or gemini, or ollama
response = await agent.chat("Explain quantum computing")
```

### MCP Integration

```python
from logicore import MCPAgent, GroqProvider

# Agent with Model Context Protocol servers
agent = MCPAgent(
    provider=GroqProvider(model_name="llama-3.3-70b-versatile"),
    mcp_config="mcp.json"
)
response = await agent.chat("Search for recent AI papers")
```

## 🛠️ Built-in Tools

Logicore ships with a comprehensive set of ready-to-use tools:

- **File System** — Read, write, list, search files
- **Web** — Search the web, fetch URLs, image search
- **Git** — Run git commands programmatically
- **Code Execution** — Execute Python and shell commands safely
- **Documents** — Read PDFs, DOCX, XLSX, and more
- **PDF Tools** — Merge and split PDF files
- **Office Tools** — Create and edit Word/Excel documents

---

## 🏗️ Agent Types

| Agent | Best For |
|-------|----------|
| `BasicAgent` | Simple single-turn tool-calling agents |
| `Agent` | Full-featured agents with memory and tools |
| `SmartAgent` | Autonomous multi-step reasoning agents |
| `CopilotAgent` | Interactive copilot with step-by-step execution |
| `MCPAgent` | Agents with dynamic MCP tool discovery |

---

## 📖 Documentation

Full documentation available at: [https://rudramodi360.github.io/Agentry/](https://rudramodi360.github.io/Agentry/)

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built with ❤️ by [RudraModi360](https://github.com/RudraModi360)**

⭐ Star this repo if you find it useful!

</div>
