"""
Integration tests for telemetry across providers and canonical normalizer.

Verifies that usage data (input_tokens, output_tokens, cache_read_tokens,
reasoning_tokens) is properly captured and reported by each provider and
normalized correctly by the canonical normalizer.
"""

import asyncio
import sys
import traceback
from decimal import Decimal

# ── Results collector ──────────────────────────────────────────────────────────
results = []

def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    marker = "+" if passed else "x"
    msg = f"[{marker}] {name}: {status}"
    if detail:
        msg += f"  ({detail})"
    results.append(msg)
    print(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: Canonical normalize_usage  (run first — no network required)
# ═══════════════════════════════════════════════════════════════════════════════
def test_canonical_normalize_usage():
    from logicore.telemetry.canonical import normalize_usage, CanonicalUsage

    # --- 5a: OpenAI Chat Completions format ---
    openai_raw = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 20},
    }
    u = normalize_usage(openai_raw, provider="openai")
    assert isinstance(u, CanonicalUsage), "Expected CanonicalUsage instance"
    assert u.input_tokens == 80, f"input_tokens={u.input_tokens}, want 80 (100-20)"
    assert u.output_tokens == 50, f"output_tokens={u.output_tokens}, want 50"
    assert u.cache_read_tokens == 20, f"cache_read_tokens={u.cache_read_tokens}, want 20"
    assert u.prompt_tokens == 100, f"prompt_tokens={u.prompt_tokens}, want 100"
    assert u.total_tokens == 150, f"total_tokens={u.total_tokens}, want 150"
    record("5a: normalize_usage(OpenAI Chat)", True, str(u.to_dict()))

    # --- 5b: Anthropic format ---
    anthropic_raw = {
        "input_tokens": 80,
        "output_tokens": 50,
        "cache_read_input_tokens": 20,
    }
    u2 = normalize_usage(anthropic_raw, provider="anthropic")
    assert u2.input_tokens == 80, f"input_tokens={u2.input_tokens}, want 80"
    assert u2.output_tokens == 50, f"output_tokens={u2.output_tokens}, want 50"
    assert u2.cache_read_tokens == 20, f"cache_read_tokens={u2.cache_read_tokens}, want 20"
    assert u2.prompt_tokens == 100, f"prompt_tokens={u2.prompt_tokens}, want 100"
    record("5b: normalize_usage(Anthropic)", True, str(u2.to_dict()))

    # --- 5c: Groq Responses format (input_tokens with details) ---
    groq_raw = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 20},
        "output_tokens_details": {"reasoning_tokens": 10},
    }
    u3 = normalize_usage(groq_raw, provider="groq")
    assert u3.input_tokens == 80, f"input_tokens={u3.input_tokens}, want 80"
    assert u3.output_tokens == 50, f"output_tokens={u3.output_tokens}, want 50"
    assert u3.cache_read_tokens == 20, f"cache_read_tokens={u3.cache_read_tokens}, want 20"
    assert u3.reasoning_tokens == 10, f"reasoning_tokens={u3.reasoning_tokens}, want 10"
    record("5c: normalize_usage(Groq Responses)", True, str(u3.to_dict()))

    # --- 5d: None → all zeros ---
    u4 = normalize_usage(None)
    assert u4.input_tokens == 0
    assert u4.output_tokens == 0
    assert u4.cache_read_tokens == 0
    assert u4.total_tokens == 0
    record("5d: normalize_usage(None)", True, str(u4.to_dict()))


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: Pricing
# ═══════════════════════════════════════════════════════════════════════════════
def test_pricing():
    from logicore.telemetry.pricing import estimate_usage_cost, has_known_pricing
    from logicore.telemetry.canonical import CanonicalUsage

    # --- 6a: Known model (gpt-4o) ---
    usage = CanonicalUsage(input_tokens=1_000_000, output_tokens=500_000)
    result = estimate_usage_cost("gpt-4o", usage, provider="openai")
    assert result.amount_usd is not None, f"amount_usd should not be None for gpt-4o"
    assert result.amount_usd > 0, f"amount_usd should be > 0, got {result.amount_usd}"
    assert has_known_pricing("gpt-4o", provider="openai"), "gpt-4o should have known pricing"
    record("6a: pricing(gpt-4o)", True, f"cost={result.label}, source={result.source}")

    # --- 6b: Anthropic model ---
    usage2 = CanonicalUsage(input_tokens=100_000, output_tokens=50_000, cache_read_tokens=10_000)
    result2 = estimate_usage_cost("claude-sonnet-4-5", usage2, provider="anthropic")
    assert result2.amount_usd is not None, f"amount_usd should not be None for claude-sonnet-4-5"
    assert result2.amount_usd > 0
    record("6b: pricing(claude-sonnet-4-5)", True, f"cost={result2.label}, source={result2.source}")

    # --- 6c: Unknown model → status "unknown" ---
    result3 = estimate_usage_cost("some-unknown-model-xyz", CanonicalUsage(input_tokens=100), provider="unknown")
    assert result3.status == "unknown", f"Expected status 'unknown', got '{result3.status}'"
    record("6c: pricing(unknown model)", True, f"status={result3.status}")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers for provider tests
# ═══════════════════════════════════════════════════════════════════════════════
def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Groq non-streaming (Responses API)
# ═══════════════════════════════════════════════════════════════════════════════
def test_groq_nonstreaming():
    from logicore.providers.groq_provider import GroqProvider
    from logicore.gateway.openai_gateway import OpenAIGateway

    provider = GroqProvider(model_name="llama-3.1-8b-instant")
    gateway = OpenAIGateway(provider)

    messages = [
        {"role": "user", "content": "Say exactly: telemetry test ok"},
    ]
    result = _run_async(gateway.chat(messages, max_tokens=50))

    usage = result.usage
    assert usage is not None, "usage must not be None"
    assert isinstance(usage, dict), f"usage should be dict, got {type(usage)}"
    assert usage.get("prompt_tokens", 0) > 0, f"prompt_tokens must be > 0, got {usage}"
    assert usage.get("completion_tokens", 0) > 0, f"completion_tokens must be > 0"

    details = usage.get("prompt_tokens_details")
    assert details is not None, "prompt_tokens_details must exist"
    assert "cached_tokens" in details, f"cached_tokens key missing in prompt_tokens_details: {details}"
    record("1: Groq non-streaming", True, f"usage={usage}")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: Groq streaming (Responses API)
# ═══════════════════════════════════════════════════════════════════════════════
def test_groq_streaming():
    from logicore.providers.groq_provider import GroqProvider
    from logicore.gateway.openai_gateway import OpenAIGateway

    provider = GroqProvider(model_name="llama-3.1-8b-instant")
    gateway = OpenAIGateway(provider)

    messages = [
        {"role": "user", "content": "Say exactly: stream telemetry ok"},
    ]
    result = _run_async(gateway.chat_stream(messages, max_tokens=50))

    usage = result.usage
    assert usage is not None, "usage must not be None (streaming)"
    assert isinstance(usage, dict), f"usage should be dict, got {type(usage)}"
    assert usage.get("prompt_tokens", 0) > 0, f"prompt_tokens must be > 0, got {usage}"
    assert usage.get("completion_tokens", 0) > 0, f"completion_tokens must be > 0"

    details = usage.get("prompt_tokens_details")
    assert details is not None, "prompt_tokens_details must exist in streaming"
    assert "cached_tokens" in details, f"cached_tokens key missing: {details}"
    record("2: Groq streaming", True, f"usage={usage}")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Ollama non-streaming
# ═══════════════════════════════════════════════════════════════════════════════
def _ollama_is_running():
    """Check if Ollama is reachable."""
    try:
        import ollama
        c = ollama.Client()
        c.list()
        return True
    except Exception:
        return False


def test_ollama_nonstreaming():
    from logicore.providers.ollama_provider import OllamaProvider
    from logicore.gateway.ollama_gateway import OllamaGateway

    provider = OllamaProvider(model_name="qwen3:0.6b")
    gateway = OllamaGateway(provider)
    gateway.think = False  # qwen3 defaults to think; disable so content is populated

    messages = [
        {"role": "user", "content": "Say exactly: ollama telemetry ok"},
    ]
    result = _run_async(gateway.chat(messages, max_tokens=50))

    usage = result.usage
    assert usage is not None, "usage must not be None (Ollama)"
    assert isinstance(usage, dict), f"usage should be dict, got {type(usage)}"
    assert usage.get("prompt_tokens", 0) > 0, f"prompt_tokens must be > 0, got {usage}"
    assert usage.get("completion_tokens", 0) > 0, f"completion_tokens must be > 0"
    record("3: Ollama non-streaming", True, f"usage={usage}")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: Ollama streaming
# ═══════════════════════════════════════════════════════════════════════════════
def test_ollama_streaming():
    from logicore.providers.ollama_provider import OllamaProvider
    from logicore.gateway.ollama_gateway import OllamaGateway

    provider = OllamaProvider(model_name="qwen3:0.6b")
    gateway = OllamaGateway(provider)

    messages = [
        {"role": "user", "content": "Say exactly: ollama stream ok"},
    ]
    result = _run_async(gateway.chat_stream(messages, max_tokens=50))

    usage = result.usage
    assert usage is not None, "usage must not be None (Ollama streaming)"
    assert isinstance(usage, dict), f"usage should be dict, got {type(usage)}"
    assert usage.get("prompt_tokens", 0) > 0, f"prompt_tokens must be > 0, got {usage}"
    assert usage.get("completion_tokens", 0) > 0, f"completion_tokens must be > 0"
    record("4: Ollama streaming", True, f"usage={usage}")


# ═══════════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    import os

    print("=" * 70)
    print("  Telemetry Integration Tests")
    print("=" * 70)

    # --- Offline tests (always run) ---
    print("\n--- Offline tests ---")
    try:
        test_canonical_normalize_usage()
    except Exception as e:
        record("5: canonical normalize_usage", False, f"{e}\n{traceback.format_exc()}")

    try:
        test_pricing()
    except Exception as e:
        record("6: pricing", False, f"{e}\n{traceback.format_exc()}")

    # --- Groq (requires GROQ_API_KEY) ---
    groq_key = os.environ.get("GROQ_API_KEY", "")
    print("\n--- Groq provider tests ---")
    if not groq_key:
        record("1: Groq non-streaming", False, "SKIPPED — GROQ_API_KEY not set")
        record("2: Groq streaming",      False, "SKIPPED — GROQ_API_KEY not set")
    else:
        try:
            test_groq_nonstreaming()
        except Exception as e:
            record("1: Groq non-streaming", False, f"{e}\n{traceback.format_exc()}")
        try:
            test_groq_streaming()
        except Exception as e:
            record("2: Groq streaming", False, f"{e}\n{traceback.format_exc()}")

    # --- Ollama (requires Ollama running locally) ---
    print("\n--- Ollama provider tests ---")
    if not _ollama_is_running():
        record("3: Ollama non-streaming", False, "SKIPPED — Ollama not running")
        record("4: Ollama streaming",     False, "SKIPPED — Ollama not running")
    else:
        try:
            test_ollama_nonstreaming()
        except Exception as e:
            record("3: Ollama non-streaming", False, f"{e}\n{traceback.format_exc()}")
        try:
            test_ollama_streaming()
        except Exception as e:
            record("4: Ollama streaming", False, f"{e}\n{traceback.format_exc()}")

    # --- Summary ---
    passed = sum(1 for r in results if "PASS" in r)
    failed = sum(1 for r in results if "FAIL" in r)
    print("\n" + "=" * 70)
    print(f"  Summary: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 70)
    for r in results:
        print(f"  {r}")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
