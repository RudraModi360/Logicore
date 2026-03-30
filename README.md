# Logicore AI Framework

<p align="center">
    <img src="./logo/readme-hero.png" alt="Logicore Banner" width="420" style="max-width:60%; height:auto;" />
</p>

<p align="center">
    <a href="https://rudramodi360.github.io/Agentry/"><img src="https://img.shields.io/badge/Docs-Live-blue.svg" alt="Documentation" /></a>
    <a href="https://discord.gg/Yz8yFzgQ"><img src="https://img.shields.io/badge/Discord-Join-7289DA.svg" alt="Discord" /></a>
    <a href="https://pypi.org/project/logicore/"><img src="https://img.shields.io/pypi/v/logicore.svg" alt="PyPI" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License" /></a>
</p>

**Logicore** is an enterprise-grade Python framework for building autonomous, intelligent AI agents that work seamlessly across any LLM provider—local (Ollama), cloud (OpenAI, Gemini, Groq, Azure), or hybrid. 

Build agents **once** → Deploy everywhere. No vendor lock-in. Zero provider-specific code.

> 💡 **New to Logicore?** → Read the [Comprehensive Introduction](./docs/introduction.md) to understand what makes this framework different.

---

## 🌟 Key Features

* **Unified Multi-Provider Architecture:** Switch between LLM backends (Ollama, Gemini, OpenAI, Groq) seamlessly. Your agent logic and tool schemas remain completely unchanged.
* **Native Streaming & Reasoning Extraction:** Advanced streaming support that pulls hidden `<think>` reasoning tokens from local models (like `qwen3.5:0.8b` and DeepSeek series) so your UI updates in real-time before tools execute.
* **First-Class Tooling:** Turn any Python function into an LLM tool automatically. Logicore parses type hints and docstrings into JSON schemas, supports `**kwargs` for hallucination-resilience, and safely reflects execution errors back to the model.
* **Built-in Cron Job Scheduler:** Endow your agents with temporal awareness. Agents can natively schedule, manage, and execute automated background tasks without external infrastructure.
* **Persistent Memory & RAG:** Equip agents with long-term conversational memory and semantic vector search so they never lose context across sessions.
* **Built-in Skills & Copilot:** Pre-packaged skill sets (Web Research, Code Review, File Manipulation) and a ready-to-use `CopilotAgent` for instant productivity.

---

## 🚀 Quickstart

Get an intelligent, tool-enabled agent running locally in two minutes.

### 1. Install Logicore
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

---

## 📚 Documentation
Comprehensive documentation for Logicore is available via our official site. It includes deep dives into Agents, Providers, Skills, Custom Tool guidelines, and a full API Reference.

👉 **[Read the Official Documentation here](https://rudramodi360.github.io/Agentry/)**

---

## 🤝 Community & Contributions
* **Discord:** Join our official server to connect with other developers: [Logicore Discord](https://discord.gg/Yz8yFzgQ)
* **Contributing:** We welcome all contributions! Please see our [Contributing Guidelines](docs/contributing.md) to get started.

---
*Built with ❤️ for multi-provider agentic workflows.*