---
title: FAQ
description: Answers to common questions about installing, configuring, and operating Logicore.
---

# FAQ

## Do I need a specific model provider?
No. Logicore lets you swap providers (OpenAI, Groq, Ollama, Gemini, Azure OpenAI, Anthropic) without changing your agent code. Pick based on cost, latency, and data residency needs.

## How do I run the docs locally?
```bash
npm install
npm run dev
```
The site reloads automatically when you edit markdown.

## Where do I configure API keys?
Use environment variables (`OPENAI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY`). Avoid hardcoding secrets.

## Does Logicore support streaming responses?
Yes. All providers expose streaming; use `stream=True` (Python) or the async API. See the Streaming guide.

## How is memory stored?
A pluggable memory layer supports SQLite/LanceDB for vectors plus session metadata. Configure in `logicore/memory/` or override in your agent init.

## How do I add a new tool?
Implement the tool interface, register it with the agent, and add capability docs. The Tool Integration guide walks through a working example.

## Can I deploy to production?
Yes. Use the Production Deployment guide for environment variables, logging/telemetry, and CI/CD. GitHub Actions workflow `mintlify-deploy.yml` publishes docs; your app deploy is separate.

## How do I report an issue?
Open a GitHub issue with:
- What you tried (code snippet)
- Expected vs actual behavior
- Provider and model
- Logs or stack trace

## Is there a community channel?
Join Discord: https://discord.gg/logicore
