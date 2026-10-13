"""Provider token report — single ping per provider, provider-reported counts.

For the six fixed providers (openai / azure / anthropic / gemini / groq /
ollama) the response usage IS the truth: this script reads those numbers via
normalize_usage and shows input / output / cache / reasoning / total. No local
estimation, so a brand-new model family keeps working with zero code changes.

Custom endpoints are the only place local counting appears, tried in order:
  1. explicit tokenizer config (CUSTOM_TOKENIZER_JSON env),
  2. HF AutoTokenizer loaded straight from the model name (no static map),
  3. heuristic fallback WITH a visible inaccuracy warning.
The live provider-reported usage is still shown alongside for comparison.

Run:
    python scripts/test_provider_tokens.py            # all providers
    python scripts/test_provider_tokens.py gemini     # one provider
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from logicore.telemetry.canonical import normalize_usage

PING_MESSAGES = [
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "assistant", "content": "Paris."},
    {"role": "user", "content": "And what is the capital of Italy?"},
]

SEPARATOR = "=" * 60

USAGE_SOURCE = {
    "openai": "provider-reported (chat.completions usage)",
    "azure": "provider-reported (azure usage)",
    "anthropic": "provider-reported (messages usage)",
    "gemini": "provider-reported (usageMetadata)",
    "groq": "provider-reported (chat.completions usage)",
    "ollama": "provider-reported (prompt_eval_count/eval_count)",
    "custom": "provider-reported (openai-compatible usage)",
}


def _show(name: str, model: str, raw_usage, canonical, elapsed: float,
          notes=None) -> None:
    """Print one provider's token report. Every number below is provider truth."""
    print(f"provider    : {name}")
    print(f"model       : {model}")
    print(f"source      : {USAGE_SOURCE.get(name, 'provider-reported')}")
    print(f"input       : {canonical.input_tokens}")
    print(f"output      : {canonical.output_tokens}")
    print(f"cache_hit   : {canonical.cache_read_tokens}")
    print(f"cache_miss  : {canonical.cache_write_tokens}")
    if canonical.reasoning_tokens:
        print(f"reasoning   : {canonical.reasoning_tokens}")
    print(f"prompt_total: {canonical.prompt_tokens}")
    print(f"total       : {canonical.total_tokens}")
    print(f"latency     : {elapsed:.1f}s")
    for note in notes or []:
        print(f"note        : {note}")
    if not raw_usage:
        print("WARNING     : provider did not report usage for this call")
        return
    # Dashboard cross-check: provider's own total vs our normalized total.
    raw_total = (raw_usage.get("total_tokens")
                 if isinstance(raw_usage, dict) else getattr(raw_usage, "total_tokens", None))
    if raw_total and raw_total != canonical.total_tokens:
        print(f"WARNING     : provider total {raw_total} != display total "
              f"{canonical.total_tokens} (unmapped buckets?)")


async def _ping(name: str, model: str, make_provider) -> None:
    print(f"\n{SEPARATOR}\n  {name} / {model}\n{SEPARATOR}")
    try:
        provider = make_provider()
    except Exception as e:
        print(f"SKIP: cannot init ({type(e).__name__}: {e})")
        return

    start = time.time()
    try:
        response = await provider.chat(PING_MESSAGES)
    except Exception as e:
        print(f"CALL FAILED: {type(e).__name__}: {str(e)[:200]}")
        return
    elapsed = time.time() - start

    raw_usage = getattr(response, "usage", None) or {}
    canonical = normalize_usage(raw_usage, provider=name)
    _show(name, model, raw_usage, canonical, elapsed)


# ---------------------------------------------------------------- fixed providers
async def ping_openai() -> None:
    from logicore.providers.openai_provider import OpenAIProvider

    if not os.environ.get("OPENAI_API_KEY"):
        print(f"\n{SEPARATOR}\n  openai / gpt-4o-mini\n{SEPARATOR}")
        print("SKIP: OPENAI_API_KEY not set")
        return
    endpoint = os.environ.get("OPENAI_BASE_URL") or None
    await _ping("openai", "gpt-4o-mini",
                lambda: OpenAIProvider(model_name="gpt-4o-mini", endpoint=endpoint))


async def ping_azure() -> None:
    from logicore.providers.azure_provider import AzureProvider

    if not os.environ.get("AZURE_API_KEY"):
        print(f"\n{SEPARATOR}\n  azure / gpt-4o\n{SEPARATOR}")
        print("SKIP: AZURE_API_KEY not set")
        return
    model = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    await _ping("azure", model, lambda: AzureProvider(model_name=model))


async def ping_anthropic() -> None:
    from logicore.providers.azure_provider import AzureProvider

    if not os.environ.get("AZURE_ANTHROPIC_ENDPOINT"):
        print(f"\n{SEPARATOR}\n  anthropic / claude-3-5-sonnet\n{SEPARATOR}")
        print("SKIP: AZURE_ANTHROPIC_ENDPOINT not set (Claude is served via Azure Foundry)")
        return
    model = os.environ.get("AZURE_ANTHROPIC_DEPLOYMENT", "claude-3-5-sonnet")
    endpoint = os.environ.get("AZURE_ANTHROPIC_ENDPOINT")
    await _ping("anthropic", model,
                lambda: AzureProvider(model_name=model, model_type="anthropic", endpoint=endpoint))


async def ping_gemini() -> None:
    from logicore.providers.gemini_provider import GeminiProvider

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    if not os.environ.get("GEMINI_API_KEY"):
        print(f"\n{SEPARATOR}\n  gemini / {model}\n{SEPARATOR}")
        print("SKIP: GEMINI_API_KEY not set")
        return
    await _ping("gemini", model, lambda: GeminiProvider(model_name=model))


async def ping_groq() -> None:
    from logicore.providers.groq_provider import GroqProvider

    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    if not os.environ.get("GROQ_API_KEY"):
        print(f"\n{SEPARATOR}\n  groq / {model}\n{SEPARATOR}")
        print("SKIP: GROQ_API_KEY not set")
        return
    await _ping("groq", model, lambda: GroqProvider(model_name=model))


async def ping_ollama() -> None:
    from logicore.providers.ollama_provider import OllamaProvider

    model = os.environ.get("OLLAMA_TEST_MODEL", "gemma4:cloud")
    endpoint = os.environ.get("OLLAMA_ENDPOINT", "https://ollama.com")
    await _ping("ollama", model, lambda: OllamaProvider(model_name=model, endpoint=endpoint))


# ---------------------------------------------------------------- custom only
def _custom_precount(messages, model: str):
    """Local pre-count chain for custom endpoints (no static maps).

    Returns (count|None, method, warnings).
    """
    from logicore.runtime.context.token_estimator import TokenEstimator

    # 1. Explicit config wins, e.g.
    #    CUSTOM_TOKENIZER_JSON='{"type":"tiktoken","encoding":"cl100k_base"}'
    cfg_raw = os.environ.get("CUSTOM_TOKENIZER_JSON", "")
    if cfg_raw:
        try:
            cfg = json.loads(cfg_raw)
            if cfg.get("type") == "tiktoken":
                import tiktoken
                enc = tiktoken.get_encoding(cfg.get("encoding", "cl100k_base"))
                blob = json.dumps(messages, separators=(",", ":"))
                return len(enc.encode(blob)), f"configured:tiktoken:{cfg.get('encoding')}", []
            return None, "configured", [f"unsupported tokenizer config type: {cfg.get('type')}"]
        except Exception as e:
            return None, "configured", [f"custom tokenizer config failed: {e}"]

    # 2-3. Provider-aware estimator: tries HF (from model name) -> tiktoken ->
    #      heuristic, surfacing an inaccuracy warning on fallback.
    estimator = TokenEstimator(provider="custom", model=model)
    count = estimator.count_messages_tokens(messages)
    method = "hf" if estimator.get_accuracy_class() in ("high", "exact") else estimator.get_accuracy_class()
    warnings = list(estimator.warnings) or [
        "count may be inaccurate — heuristic fallback (~4 chars/token); treat as rough estimate only"
    ]
    return count, method, warnings


async def ping_custom() -> None:
    from logicore.providers.custom_provider import CustomProvider

    endpoint = os.environ.get("CUSTOM_PROVIDER_ENDPOINT", "https://opencode.ai/zen/v1")
    model = os.environ.get("CUSTOM_PROVIDER_MODEL", "space-bunny-free")
    api_key = os.environ.get("CUSTOM_PROVIDER_API_KEY", "")

    print(f"\n{SEPARATOR}\n  custom / {model}\n{SEPARATOR}")
    if not api_key:
        print("SKIP: CUSTOM_PROVIDER_API_KEY not set")
        return
    try:
        provider = CustomProvider(model_name=model, api_key=api_key, endpoint=endpoint)
    except Exception as e:
        print(f"SKIP: cannot init ({type(e).__name__}: {e})")
        return

    pre_count, method, warnings = _custom_precount(PING_MESSAGES, model)
    print(f"local pre-count : {pre_count}  (method={method})")
    for w in warnings:
        print(f"WARNING         : {w}")

    start = time.time()
    try:
        response = await provider.chat(PING_MESSAGES)
    except Exception as e:
        print(f"CALL FAILED: {type(e).__name__}: {str(e)[:200]}")
        return
    elapsed = time.time() - start

    raw_usage = getattr(response, "usage", None) or {}
    canonical = normalize_usage(raw_usage, provider="custom")
    _show("custom", model, raw_usage, canonical, elapsed)


PINGS = {
    "openai": ping_openai,
    "azure": ping_azure,
    "anthropic": ping_anthropic,
    "gemini": ping_gemini,
    "groq": ping_groq,
    "ollama": ping_ollama,
    "custom": ping_custom,
}


async def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]] or list(PINGS)
    unknown = [w for w in wanted if w not in PINGS]
    if unknown:
        print(f"Unknown: {unknown}. Available: {list(PINGS)}")
        return
    for name in wanted:
        await PINGS[name]()
    print(f"\n{SEPARATOR}\n  done\n{SEPARATOR}")


if __name__ == "__main__":
    asyncio.run(main())
