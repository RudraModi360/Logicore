---
title: Providers
description: Compare providers and plug them into Logicore without code changes.
---

# Providers

Logicore treats each LLM vendor as a swappable provider. Your agent code stays the same; you inject a different provider instance to change cost, latency, or data residency.

## Comparison at a glance

| Provider | Best for | Typical latency | Cost | Notes |
| --- | --- | --- | --- | --- |
| OpenAI | Highest quality, broad ecosystem | Fast | $$$ | Great default; strong tools support |
| Groq | Speed + low cost | Ultra fast | $ | Ideal for high-volume chat |
| Ollama | Local / air-gapped | Hardware dependent | Free | No external calls; good for privacy |
| Gemini | Vision + multimodal | Fast | $$ | Excellent image + text reasoning |
| Anthropic (Claude) | Long context + reasoning | Fast | $$ | Strong safety and analysis |
| Azure OpenAI | Enterprise, regional control | Fast | $$$ | Same models with Azure compliance |

## Choose your provider
1) **Latency-sensitive?** ? Groq first, then OpenAI/Gemini.
2) **Lowest cost?** ? Groq, then Ollama (if GPU available).
3) **Privacy/regional requirements?** ? Azure OpenAI or Ollama on-prem.
4) **Vision/multimodal?** ? Gemini or OpenAI gpt-4o.
5) **Long, reasoning-heavy docs?** ? Anthropic Claude.

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
