---
title: Overview
description: Compare providers and plug them into Logicore without code changes.
---

# Providers

Logicore treats each LLM vendor as a swappable provider. Your agent code stays the same; you inject a different provider instance to change cost, latency, or data residency.

## Direct provider usage guides

Use the provider-specific guides below to integrate each provider directly into `Agent`.

- [Ollama Provider](./provider-ollama.md)
- [Groq Provider](./provider-groq.md)
- [Gemini Provider](./provider-gemini.md)
- [Azure Provider](./provider-azure.md)
- [OpenAI Provider](./provider-openai.md)

## Comparison at a glance

| Provider | Best for | Typical latency | Cost | Multimodal | Guide |
| --- | --- | --- | --- | --- | --- |
| OpenAI | Highest quality, broad ecosystem | Fast | $$$ | Yes (vision models) | [OpenAI](./provider-openai.md) |
| Groq | Speed + low cost | Ultra fast | $ | Yes (vision models) | [Groq](./provider-groq.md) |
| Ollama | Local / air-gapped | Hardware dependent | Free | Yes (vision models) | [Ollama](./provider-ollama.md) |
| Gemini | Vision + multimodal | Fast | $$ | Yes | [Gemini](./provider-gemini.md) |
| Azure | Enterprise, regional control | Fast | $$$ | Yes (deployment dependent) | [Azure](./provider-azure.md) |

## Canonical multimodal message format

Use the same input structure across providers:

```python
message = [
    {"type": "text", "text": "Describe this image."},
    {"type": "image_url", "image_url": r"D:\\images\\sample.png"}
]

response = await agent.chat(message)
```

`image_url` can be a local path, `https://` URL, or `data:image/...;base64,...`.

## Choose your provider
1) **Latency-sensitive?** Groq first, then OpenAI/Gemini.
2) **Lowest cost?** Groq, then Ollama (if GPU available).
3) **Privacy/regional requirements?** Azure OpenAI or Ollama on-prem.
4) **Vision/multimodal?** Gemini or OpenAI gpt-4o.
5) **Long, reasoning-heavy docs?** Anthropic Claude.

## Using providers

```python
from logicore import Agent
from logicore.providers import OpenAIProvider, GroqProvider

# Swap providers without changing your agent logic
provider = GroqProvider(model="llama-3.3-70b-versatile", api_key="${GROQ_API_KEY}")
# provider = OpenAIProvider(model="gpt-4o", api_key="${OPENAI_API_KEY}")

agent = Agent(provider=provider)
print(agent.chat("Give me a 2-line TL;DR"))
```

### Common options
- `temperature`, `max_tokens`, `timeout`
- `tools`: list of Tool instances
- `metadata`: passed through to logs/telemetry

### Failover pattern
```python
def make_agent(primary, fallback):
    try:
        return Agent(provider=primary)
    except Exception:
        return Agent(provider=fallback)

agent = make_agent(
    primary=GroqProvider(model="llama-3.3-70b-versatile"),
    fallback=OpenAIProvider(model="gpt-4o")
)
```

> Tip: capture per-provider latency and error rate; route traffic dynamically based on those metrics.
