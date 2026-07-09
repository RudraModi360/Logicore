"""
Example: interactive terminal streaming CLI with multi-provider support.

Supports:
    - Agent API mode (full agent with tools, context, events)
    - Raw provider mode (direct SDK streaming, no agent overhead)

Providers:
    - ollama: Local models via Ollama (default for local models)
    - groq: Groq API (fast inference)
    - gemini: Google Gemini API
    - openai: OpenAI API
    - azure: Azure OpenAI
    - custom: Any OpenAI-compatible endpoint (vLLM, LM Studio, etc.)

Run:
    python examples/streaming_cli.py
    python examples/streaming_cli.py --provider ollama --model llama3.2:3b
    python examples/streaming_cli.py --provider groq --model meta-llama/llama-4-scout-17b-16e-instruct
    python examples/streaming_cli.py --provider gemini --model gemini-2.5-flash
    python examples/streaming_cli.py --provider custom --model my-model --endpoint http://localhost:1234/v1
    python examples/streaming_cli.py --raw --provider ollama --model llama3.2:3b
    python examples/streaming_cli.py --debug
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from logicore.agent import Agent
from logicore.stream.events import StreamEventType


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def create_provider(provider_name: str, model: str, endpoint: Optional[str] = None, api_key: Optional[str] = None):
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
        ep = endpoint or os.environ.get("CUSTOM_PROVIDER_ENDPOINT") or os.environ.get("CUSTOM_MODEL_ENDPOINT")
        if not ep:
            raise ValueError("Custom provider requires --endpoint or CUSTOM_PROVIDER_ENDPOINT env var")
        return CustomProvider(model_name=model, endpoint=ep, api_key=api_key)
    
    else:
        raise ValueError(f"Unknown provider: {provider_name}. Supported: ollama, groq, gemini, openai, azure, custom")


def create_gateway(provider):
    """Create the appropriate gateway for a provider."""
    provider_name = getattr(provider, "provider_name", "unknown")
    
    if provider_name == "ollama":
        from logicore.gateway.ollama_gateway import OllamaGateway
        return OllamaGateway(provider=provider)
    elif provider_name == "groq":
        from logicore.gateway.openai_gateway import OpenAIGateway
        return OpenAIGateway(provider=provider)
    elif provider_name == "gemini":
        from logicore.gateway.gemini_gateway import GeminiGateway
        return GeminiGateway(provider=provider)
    elif provider_name in ("openai", "custom"):
        from logicore.gateway.openai_gateway import OpenAIGateway
        return OpenAIGateway(provider=provider)
    elif provider_name == "azure":
        from logicore.gateway.azure_gateway import AzureGateway
        return AzureGateway(provider=provider)
    else:
        from logicore.gateway.openai_gateway import OpenAIGateway
        return OpenAIGateway(provider=provider)


# ---------------------------------------------------------------------------
# Raw mode — direct provider streaming, no Agent
# ---------------------------------------------------------------------------

def _raw_stream(provider, model: str, prompt: str, debug: bool = False) -> None:
    """Stream tokens directly from the provider SDK."""
    import asyncio
    from logicore.stream.events import StreamEvent
    
    gateway = create_gateway(provider)
    messages = [{"role": "user", "content": prompt}]
    
    events = []
    
    def collect_event(event):
        events.append(event)
    
    async def stream():
        await gateway.chat_stream(messages=messages, on_event=collect_event)
    
    asyncio.run(stream())
    
    # Process events
    in_thinking = False
    for event in events:
        event_type = event.get("type")
        data = event.get("data", {})
        
        if event_type == "token":
            if in_thinking:
                print("</THINKING>", flush=True)
                in_thinking = False
            print(data.get("delta", ""), end="", flush=True)
        
        elif event_type == "reasoning":
            if not in_thinking:
                print("<THINKING>", end="", flush=True)
                in_thinking = True
            print(data.get("delta", ""), end="", flush=True)
        
        elif event_type == "tool_call_chunk":
            if in_thinking:
                print("</THINKING>", flush=True)
                in_thinking = False
            if data.get("args_delta") == "":
                print(f"\n[tool] {data.get('name')}()", flush=True)
        
        elif event_type == "error":
            if in_thinking:
                print("</THINKING>", flush=True)
                in_thinking = False
            print(f"\n[error] {data.get('message')}", flush=True)
        
        elif debug:
            print(f"\n[{event_type}] {data}", flush=True)
    
    if in_thinking:
        print("</THINKING>", flush=True)
    print()


# ---------------------------------------------------------------------------
# Agent mode — full Agent with streaming events
# ---------------------------------------------------------------------------

def _agent_stream_sync(agent: Agent, prompt: str, debug: bool = False) -> None:
    """Synchronous wrapper for agent streaming."""
    in_thinking = [False]

    def on_event(ev):
        if ev.type == StreamEventType.TOKEN:
            if in_thinking[0]:
                print("</THINKING>", flush=True)
                in_thinking[0] = False
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.REASONING:
            if not in_thinking[0]:
                print("<THINKING>", end="", flush=True)
                in_thinking[0] = True
            print(ev.data.get("delta", ""), end="", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_CHUNK:
            if in_thinking[0]:
                print("</THINKING>", flush=True)
                in_thinking[0] = False
            if ev.data.get("args_delta") == "":
                print(f"\n[tool] {ev.data.get('name')}()", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_START:
            if in_thinking[0]:
                print("</THINKING>", flush=True)
                in_thinking[0] = False
            print(f"\n[tool:start] {ev.data.get('name')}({ev.data.get('args')})", flush=True)
        elif ev.type == StreamEventType.TOOL_CALL_END:
            status = "ok" if ev.data.get("success") else "FAILED"
            preview = ev.data.get("preview", "")
            print(f"[tool:{status}] {preview[:120]}", flush=True)
        elif ev.type == StreamEventType.ERROR:
            if in_thinking[0]:
                print("</THINKING>", flush=True)
                in_thinking[0] = False
            print(f"\n[error] {ev.data.get('message')}", flush=True)
        elif ev.type == StreamEventType.DONE:
            if in_thinking[0]:
                print("</THINKING>", flush=True)
                in_thinking[0] = False
        elif debug:
            print(f"\n[{ev.type}] {ev.data}", flush=True)

    agent.stream_sync(prompt, on_event=on_event)
    if in_thinking[0]:
        print("</THINKING>", flush=True)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    
    parser = argparse.ArgumentParser(description="Logicore Streaming CLI")
    parser.add_argument("--provider", "-p", default="groq", 
                       choices=["ollama", "groq", "gemini", "openai", "azure", "custom"],
                       help="Provider to use (default: groq)")
    parser.add_argument("--model", "-m", default=None, help="Model name")
    parser.add_argument("--endpoint", "-e", default=None, help="Custom provider endpoint URL")
    parser.add_argument("--api-key", "-k", default=None, help="API key (or use env var)")
    parser.add_argument("--raw", action="store_true", help="Use raw provider mode (no agent)")
    parser.add_argument("--debug", "-d", action="store_true", help="Show debug events")
    parser.add_argument("--auto-approve", action="store_true", default=True,
                       help="Auto-approve tool calls (default: True)")
    
    args = parser.parse_args()
    
    # Default models per provider
    DEFAULT_MODELS = {
        "ollama": "gpt-oss:20b-cloud",
        "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o-mini",
        "azure": "gpt-4o-mini",
        "custom": "default",
    }
    
    model = args.model or DEFAULT_MODELS.get(args.provider, "default")
    
    title = f"Logicore Streaming CLI ({args.provider.upper()})"
    mode = "Raw Provider" if args.raw else "Agent API"
    print(f"{title}")
    print(f"Model: {model}  |  Mode: {mode}")
    print(f"Type 'quit' to exit")
    print(f"{'=' * 60}")
    
    # Create provider
    try:
        provider = create_provider(args.provider, model, args.endpoint, args.api_key)
    except Exception as e:
        print(f"Error creating provider: {e}")
        sys.exit(1)
    
    if args.raw:
        # Raw mode - direct provider streaming
        stream_fn = lambda prompt: _raw_stream(provider, model, prompt, args.debug)
    else:
        # Agent mode - full agent with tools
        agent = Agent(provider=provider)
        agent.tool_executor.auto_approve_all = args.auto_approve
        stream_fn = lambda prompt: _agent_stream_sync(agent, prompt, args.debug)
    
    while True:
        try:
            prompt = input("\nYou: ").strip()
            if prompt.lower() in ("quit", "exit", "q"):
                print("\nGoodbye!")
                break
            if not prompt:
                continue
            stream_fn(prompt)
        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except EOFError:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\n[error] {e}")
            if args.debug:
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    main()
