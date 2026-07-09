"""
Example: expose Logicore's streaming agent to a browser over SSE.

NOTE: a server is OPTIONAL. Streaming works in-process with no server at all
(see examples/streaming_cli.py and Agent.stream() / Agent.stream_sync()). This
file only shows one way to push the same event stream to a web frontend.

Run:
    pip install fastapi uvicorn
    uvicorn examples.streaming_chatbot:app --reload

    # With specific provider/model
    uvicorn examples.streaming_chatbot:app --reload -- --provider groq --model meta-llama/llama-4-scout-17b-16e-instruct

Then in the browser / curl:
    curl -N -X POST "http://localhost:8000/chat?q=summarize%20this%20repo&session_id=demo"

Each line is an SSE frame:  data: {"type": "...", "data": {...}, ...}

The full event model is documented in docs/concepts/streaming.md.
"""

from __future__ import annotations

import asyncio
import sys
import os
from typing import AsyncIterable

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse

from logicore import Agent, as_sse  # as_sse from logicore.stream.sse


# ---------------------------------------------------------------------------
# Parse command line arguments for provider/model
# ---------------------------------------------------------------------------

def parse_args():
    """Parse command line arguments for provider and model."""
    provider = "groq"  # default
    model = None
    endpoint = None
    api_key = None
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("--provider", "-p") and i + 1 < len(args):
            provider = args[i + 1]
            i += 2
        elif args[i] in ("--model", "-m") and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        elif args[i] in ("--endpoint", "-e") and i + 1 < len(args):
            endpoint = args[i + 1]
            i += 2
        elif args[i] in ("--api-key", "-k") and i + 1 < len(args):
            api_key = args[i + 1]
            i += 2
        else:
            i += 1
    
    # Default models per provider
    DEFAULT_MODELS = {
        "ollama": "llama3.2:3b",
        "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o-mini",
        "azure": "gpt-4o-mini",
        "custom": "default",
    }
    
    if model is None:
        model = DEFAULT_MODELS.get(provider, "default")
    
    return provider, model, endpoint, api_key


def create_provider(provider_name: str, model: str, endpoint: str = None, api_key: str = None):
    """Create a provider instance based on name."""
    if provider_name == "ollama":
        from logicore.providers.ollama_provider import OllamaProvider
        return OllamaProvider(model_name=model)
    elif provider_name == "groq":
        from logicore.providers.groq_provider import GroqProvider
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY environment variable required for Groq provider")
        return GroqProvider(model_name=model, api_key=key)
    elif provider_name == "gemini":
        from logicore.providers.gemini_provider import GeminiProvider
        return GeminiProvider(model_name=model)
    elif provider_name == "openai":
        from logicore.providers.openai_provider import OpenAIProvider
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable required for OpenAI provider")
        return OpenAIProvider(model_name=model, api_key=key)
    elif provider_name == "azure":
        from logicore.providers.azure_provider import AzureProvider
        return AzureProvider(model_name=model)
    elif provider_name == "custom":
        from logicore.providers.custom_provider import CustomProvider
        ep = endpoint or os.environ.get("CUSTOM_PROVIDER_ENDPOINT")
        if not ep:
            raise ValueError("Custom provider requires --endpoint or CUSTOM_PROVIDER_ENDPOINT env var")
        return CustomProvider(model_name=model, endpoint=ep, api_key=api_key)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


# ---------------------------------------------------------------------------
# Initialize agent with parsed args
# ---------------------------------------------------------------------------

PROVIDER, MODEL, ENDPOINT, API_KEY = parse_args()

print(f"[Streaming Chatbot] Provider: {PROVIDER}, Model: {MODEL}")

provider = create_provider(PROVIDER, MODEL, ENDPOINT, API_KEY)
agent = Agent(provider=provider, debug=False)
agent.tool_executor.auto_approve_all = True  # Auto-approve tools for smooth streaming

app = FastAPI(title="Logicore Streaming Chatbot")


@app.post("/chat")
async def chat(
    q: str = Query(..., description="User prompt"),
    session_id: str = Query("default"),
):
    """
    Stream the agent run as Server-Sent Events.

    The agent loop runs as a background task (via ``agent.stream_run``); we just
    drain its events and re-serialize them as SSE frames. A broken client only
    affects this drain loop — the run is cancelled cleanly via ``run.cancel()``.
    """
    run = await agent.stream_run(q, session_id=session_id)

    async def event_source() -> AsyncIterable[str]:
        try:
            async for frame in as_sse(run.stream_events()):
                yield frame
        except asyncio.CancelledError:
            run.cancel()
            raise

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok", "provider": PROVIDER, "model": MODEL}


# ---------------------------------------------------------------------------
# Run with Python directly (uvicorn alternative)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    
    print(f"\n{'=' * 60}")
    print(f"Logicore Streaming Chatbot")
    print(f"Provider: {PROVIDER}  |  Model: {MODEL}")
    print(f"{'=' * 60}")
    print(f"Starting server at http://localhost:8000")
    print(f"Health check: http://localhost:8000/health")
    print(f"Chat endpoint: POST http://localhost:8000/chat?q=<your-prompt>")
    print(f"{'=' * 60}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
