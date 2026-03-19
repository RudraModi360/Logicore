---
title: "Custom Agents"
description: "Guidelines and best practices for creating powerful, optimized custom agents with Logicore."
---

# Building Custom Agents

Creating a custom agent allows you to tailor Logicore's capabilities to specific domains, ensuring it behaves predictably, securely, and efficiently. 

While Logicore's built-in `CopilotAgent` is highly versatile, deploying a focused "Custom Agent" is highly recommended for production systems, especially when using smaller local models (like `qwen3.5:0.8b` or `llama3.2:1b`).

## 1. Keep Toolsets Small & Focused
A common mistake is loading an agent with dozens of tools "just in case." 

Massive system prompts filled with 50+ tool schemas consume significant context window space. For local, smaller parameter models, this can cause the initial prompt evaluation phase to take 30-60+ seconds before the first token is streamed, making the agent feel "frozen" or hung to the user.

**Guideline:** 
Only provide the exact tools necessary for the Agent's specific role. If you are building a Finance Assistant, give it `read_database` and `email_user`. Do not load general web search or file manipulation tools unless strictly required.

## 2. Leverage Model Streaming
When using custom tools, enabling streaming drastically improves User Experience (UX).

Logicore's providers (like the `OllamaProvider`) support native streaming. When an agent requires time to "think" before executing a tool, streaming allows you to surface the `<think>` reasoning tokens directly to the user in real-time.

```python
# Enable streaming by passing an on_token callback
def on_token(token):
    print(token, end="", flush=True)

await agent.chat("Check my balance", callbacks={"on_token": on_token}, stream=True)
```

## 3. Clear System Prompts & Restrictions
Small models are prone to hallucination if instructions are ambiguous. 

When defining your agent's `role` and `system_message`, be explicit about optimization constraints. 

**Bad:**
> "You are a helpful assistant. Help the user."

**Good:**
> "You are a Finance Assistant. 
> 1. Call `read_database` exactly ONCE to fetch data.
> 2. Call `email_user` exactly ONCE to notify the user.
> 3. Do not hallucinate or guess data. Use the tools."

## 4. Handle Tool Arguments Robustly
Small LLMs occasionally hallucinate extra arguments (like internal reasoning strings) when calling tools.

To prevent your agent from crashing due to unexpected arguments, always accept `**kwargs` in your custom tool definitions.

```python
def check_inventory(item_id: str, **kwargs) -> str:
    """Checks inventory. The kwargs absorb hallucinated arguments cleanly."""
    return f"{item_id} is in stock."
```

By following these guidelines, you can ensure your Logicore agents remain fast, responsive, and precise regardless of the underlying LLM's size.
