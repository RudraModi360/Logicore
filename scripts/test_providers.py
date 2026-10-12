"""Ping each provider with a hi/hello request and report the response.

Usage:
    python scripts/test_providers.py            # ping all providers
    python scripts/test_providers.py ollama gemini
"""

import asyncio
import os
import sys
import time

from logicore.providers.base import LLMProvider

PING = [{"role": "user", "content": "Hi! Reply with just 'hello' and nothing else."}]


async def _ping(name: str, provider: LLMProvider) -> bool:
    print(f"\n{'='*50}")
    print(f"  {name}  ({provider.get_model_name()})")
    print(f"{'='*50}")
    profile = provider.get_model_profile()
    if profile:
        print(f"  profile: tier={profile.tier} window={profile.context_length} source={profile.source}")
    start = time.time()
    try:
        reply = await provider.chat(PING)
        text = getattr(reply, "content", None) or str(reply)
        text = str(text).strip()
        elapsed = time.time() - start
        print(f"  OK  ({elapsed:.1f}s)")
        print(f"  -> {text[:120]}")
        return True
    except Exception as e:
        elapsed = time.time() - start
        print(f"  FAIL ({elapsed:.1f}s)")
        print(f"  -> {type(e).__name__}: {str(e)[:120]}")
        return False


def _need(key: str, label: str) -> bool:
    if os.environ.get(key):
        return True
    print(f"\n  SKIP {label}: {key} not set")
    return False


async def ping_ollama() -> bool:
    from logicore.providers.ollama_provider import OllamaProvider
    model = os.environ.get("OLLAMA_TEST_MODEL", "gemma4:cloud")
    endpoint = os.environ.get("OLLAMA_ENDPOINT")  # e.g. https://ollama.com for cloud
    return await _ping("ollama", OllamaProvider(model_name=model, endpoint=endpoint))


async def ping_gemini() -> bool:
    if not _need("GEMINI_API_KEY", "gemini"):
        return False
    from logicore.providers.gemini_provider import GeminiProvider
    return await _ping("gemini", GeminiProvider(model_name="gemini-2.5-flash"))


async def ping_groq() -> bool:
    if not _need("GROQ_API_KEY", "groq"):
        return False
    from logicore.providers.groq_provider import GroqProvider
    return await _ping("groq", GroqProvider(model_name="llama-3.3-70b-versatile"))


async def ping_openai() -> bool:
    if not _need("OPENAI_API_KEY", "openai"):
        return False
    from logicore.providers.openai_provider import OpenAIProvider
    return await _ping("openai", OpenAIProvider(model_name="gpt-4o-mini"))


async def ping_custom() -> bool:
    from logicore.providers.custom_provider import CustomProvider
    endpoint = os.environ.get("CUSTOM_PROVIDER_ENDPOINT")
    if not endpoint:
        print("\n  SKIP custom: CUSTOM_PROVIDER_ENDPOINT not set")
        return False
    model = os.environ.get("CUSTOM_PROVIDER_MODEL", "qwen3.5:0.8b")
    provider = CustomProvider(model_name=model, endpoint=endpoint)
    return await _ping("custom", provider)


async def ping_azure() -> bool:
    if not _need("AZURE_API_KEY", "azure"):
        return False
    from logicore.providers.azure_provider import AzureProvider
    model = os.environ.get("AZURE_DEPLOYMENT")
    endpoint = os.environ.get("AZURE_ENDPOINT")
    if not model or not endpoint:
        print("\n  SKIP azure: AZURE_DEPLOYMENT / AZURE_ENDPOINT not set")
        return False
    return await _ping("azure", AzureProvider(model_name=model, endpoint=endpoint))


PROVIDERS = {
    "ollama": ping_ollama,
    "gemini": ping_gemini,
    "groq": ping_groq,
    "openai": ping_openai,
    "custom": ping_custom,
    "azure": ping_azure,
}


async def main():
    wanted = [a.lower() for a in sys.argv[1:]] or list(PROVIDERS)
    unknown = [w for w in wanted if w not in PROVIDERS]
    if unknown:
        print(f"Unknown providers: {unknown}. Available: {list(PROVIDERS)}")
        return

    results = {}
    for name in wanted:
        results[name] = await PROVIDERS[name]()

    print(f"\n{'='*50}")
    print("  SUMMARY")
    print(f"{'='*50}")
    for name, ok in results.items():
        print(f"  {name:10s} {'PASS' if ok else 'FAIL/SKIP'}")
    print()


asyncio.run(main())
