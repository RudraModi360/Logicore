"""Token counting check — wires each provider's live endpoint + the new
provider-aware tokenizer strategies, then reconciles estimated input tokens
against the provider's actual reported usage.

Run:
    python experiments/token_counting_check.py            # all sections
    python experiments/token_counting_check.py routing     # one section
    python experiments/token_counting_check.py live        # live reconcile only

Sections:
    routing   - registry resolution for all 7 provider types
    tiktoken  - known-string ground truth (o200k / cl100k)
    heuristic - heuristic vs tiktoken on English prose (delta target <=15%)
    live      - estimate vs actual usage per live provider, via SessionTokenLedger
    gemini    - async countTokens vs actual (provider_reported)
    custom    - custom tokenizer config modes
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tokenizer import ProviderTokenizerRegistry, TokenCountResult, get_tokenizer
from token_estimator import TokenEstimator
from session_ledger import LedgerHub

from logicore.telemetry.canonical import normalize_usage

SECTIONS = ("routing", "tiktoken", "heuristic", "live", "gemini", "custom")

TEST_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant. Keep answers short."},
    {"role": "user", "content": "Hi! Say hello and nothing else."},
]

LONG_MESSAGES = [
    {"role": "system", "content": "You are a weather assistant."},
    {"role": "user", "content": "What is the weather in Paris right now?"},
]

TEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        },
    }
]


def hr(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def section_routing() -> None:
    hr("SECTION: registry routing (7 provider types)")
    registry = ProviderTokenizerRegistry()
    cases = [
        ("openai", "gpt-4o"),
        ("openai", "gpt-4o-mini"),
        ("openai", "gpt-3.5-turbo"),
        ("azure", "my-gpt4-deployment"),
        ("azure", "claude-3-5-sonnet-deployment"),
        ("anthropic", "claude-3-5-sonnet"),
        ("gemini", "gemini-2.5-flash"),
        ("groq", "llama-3.3-70b-versatile"),
        ("groq", "mixtral-8x7b-32768"),
        ("ollama", "qwen3.5:0.8b"),
        ("ollama", "llama3.2:3b"),
        ("ollama", "gpt-oss:20b"),
        ("custom", "my-endpoint-model"),
        ("totally-unknown", "whatever"),
    ]
    for provider, model in cases:
        strat = registry.get(provider, model)
        info = strat.describe()
        warning = info.get("warning", "")
        print(f"  {provider:14s} {model:32s} -> {info['strategy']:18s} "
              f"acc={info['accuracy_class']:9s} {warning}")


def section_tiktoken() -> None:
    hr("SECTION: tiktoken ground truth")
    estimators = [
        ("openai", "gpt-4o"),
        ("openai", "gpt-4"),
        ("openai", "gpt-3.5-turbo"),
    ]
    probe = "Hello, how are you today? I hope you are doing well."
    for provider, model in estimators:
        est = TokenEstimator(provider=provider, model=model)
        n = est.count_tokens(probe)
        print(f"  {model:16s} ({est.describe().get('encoding', '?'):10s}) -> {n:3d} tokens  |  acc={est.get_accuracy_class()}")
    legacy = TokenEstimator()
    print(f"  legacy zero-arg   (cl100k     ) -> {legacy.count_tokens(probe):3d} tokens  |  acc={legacy.get_accuracy_class()}  [BC check]")


def section_heuristic() -> None:
    hr("SECTION: heuristic vs tiktoken (English prose, target delta <=15%)")
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    sentences = [
        "The quick brown fox jumps over the lazy dog.",
        "Machine learning models convert text into numerical token representations.",
        "Please summarize the quarterly revenue report for the finance team.",
        "Artificial intelligence is transforming how software gets built today.",
        "The weather in Paris is sunny with a light breeze this afternoon.",
        "Remember to push your changes to the main branch before the meeting.",
        "A journey of a thousand miles begins with a single step forward.",
        "The system should gracefully handle network timeouts and retries.",
        "Write a unit test that covers the edge case of an empty input list.",
        "Good documentation explains not just what code does, but why.",
    ]
    hub = LedgerHub()
    ledger = hub.get_ledger("heuristic-bench", provider="unknown", model="english-prose")
    for i, sentence in enumerate(sentences, 1):
        actual = len(enc.encode(sentence, disallowed_special=()))
        est = TokenEstimator(provider="unknown", model="default")
        estimated = est.count_tokens(sentence)
        ledger.record_turn(
            turn_id=f"heur-{i}",
            estimated_input=estimated,
            actual_input=actual,
            actual_output=0,
            tokenizer_strategy="heuristic_unknown",
            accuracy_class="estimated",
        )
        delta = abs(estimated - actual) / actual * 100 if actual else 0
        flag = "OK " if delta <= 15 else "BAD"
        print(f"  {flag} [{i:2d}] actual={actual:3d} est={estimated:3d} delta={delta:5.1f}%  | {sentence[:52]}")
    summary = ledger.get_summary()
    print(f"\n  session running_delta_pct={summary['running_delta_pct']}%  "
          f"within_15%={'YES' if summary['running_delta_pct'] <= 15 else 'NO'}  "
          f"max={summary['max_delta_pct']}%")
    hint = ledger.calibration_hint()
    if hint:
        print(f"  hint: {hint}")


def _live_providers():
    """Yield (name, provider_instance, session_id) for every live-capable provider."""
    from logicore.providers.ollama_provider import OllamaProvider

    ollama_model = os.environ.get("OLLAMA_TEST_MODEL", "gemma4:cloud")
    ollama_endpoint = os.environ.get("OLLAMA_ENDPOINT", "https://ollama.com")
    yield "ollama", OllamaProvider(model_name=ollama_model, endpoint=ollama_endpoint), f"live-ollama"

    if os.environ.get("GEMINI_API_KEY"):
        from logicore.providers.gemini_provider import GeminiProvider

        yield "gemini", GeminiProvider(model_name="gemini-2.5-flash"), "live-gemini"

    if os.environ.get("GROQ_API_KEY"):
        from logicore.providers.groq_provider import GroqProvider

        yield "groq", GroqProvider(model_name="llama-3.3-70b-versatile"), "live-groq"

    if os.environ.get("OPENAI_API_KEY"):
        from logicore.providers.openai_provider import OpenAIProvider

        yield "openai", OpenAIProvider(model_name="gpt-4o-mini"), "live-openai"


async def section_live() -> None:
    hr("SECTION: live reconcile (estimated vs provider-reported usage)")
    hub = LedgerHub()
    for name, provider, session_id in _live_providers():
        model = provider.get_model_name()
        estimator = TokenEstimator(provider=name, model=model)
        described = estimator.describe()
        print(f"\n  --- {name} / {model} ---")
        print(f"  strategy={described.get('strategy')} acc={estimator.get_accuracy_class()}")

        for turn_no, (label, messages, tools) in enumerate(
            [("messages-only", TEST_MESSAGES, None), ("messages+tools", LONG_MESSAGES, TEST_TOOLS)],
            start=1,
        ):
            estimated = estimator.count_messages_tokens(messages)
            if tools:
                estimated += estimator.count_tool_tokens(tools)
            print(f"  [{label}] estimated input: {estimated}"
                  f"{'  (incl. tools)' if tools else ''}")

            start = time.time()
            try:
                response = await provider.chat(messages, tools=tools)
            except Exception as e:
                print(f"  [{label}] CALL FAILED: {type(e).__name__}: {str(e)[:110]}")
                continue
            elapsed = time.time() - start
            usage = getattr(response, "usage", None) or {}
            canonical = normalize_usage(usage, provider=name)
            print(f"  [{label}] actual usage: input={canonical.prompt_tokens} "
                  f"output={canonical.output_tokens}  ({elapsed:.1f}s)")

            record = hub.record_turn_estimation(
                session_id=session_id,
                turn_id=str(turn_no),
                estimated_input=estimated,
                canonical=canonical,
                strategy=str(described.get("strategy")),
                accuracy_class=estimator.get_accuracy_class(),
                provider=name,
                model=model,
            )
            within = "WITHIN 5%" if record.delta_pct <= 5 else "OVER 5% TARGET"
            print(f"  [{label}] delta: {record.delta_tokens} tokens ({record.delta_pct:.1f}%)  {within}")

    hr("SESSION SUMMARIES")
    for summary in hub.all_summaries():
        print(f"  {summary['provider']:8s} {summary['model']:24s} "
              f"turns={summary['turns']} running_delta={summary['running_delta_pct']}% "
              f"within_target={summary['is_within_target']} max={summary['max_delta_pct']}%")


async def section_gemini() -> None:
    hr("SECTION: gemini async countTokens vs actual")
    if not os.environ.get("GEMINI_API_KEY"):
        print("  SKIP: GEMINI_API_KEY not set")
        return
    from logicore.providers.gemini_provider import GeminiProvider
    from tokenizer.google_strategy import GoogleStrategy

    provider = GeminiProvider(model_name="gemini-2.5-flash")
    strategy = GoogleStrategy(model="gemini-2.5-flash")

    sync_result = strategy.count_messages_result(TEST_MESSAGES)
    print(f"  sync  heuristic: {sync_result.input_tokens} tokens "
          f"(method={sync_result.method} exactness={sync_result.exactness})")

    client = getattr(provider, "client", None)
    async_result = await strategy.count_async(TEST_MESSAGES, tools=None, client=client)
    print(f"  async countTokens: {async_result.input_tokens} tokens "
          f"(method={async_result.method} exactness={async_result.exactness})")
    for w in async_result.warnings:
        print(f"    warning: {w}")

    tools_result = await strategy.count_async(TEST_MESSAGES, tools=TEST_TOOLS, client=client)
    print(f"  async countTokens + tools: {tools_result.input_tokens} tokens "
          f"(native text + heuristic tools; API rejects tools config)")
    for w in tools_result.warnings:
        print(f"    warning: {w}")

    response = await provider.chat(TEST_MESSAGES)
    canonical = normalize_usage(getattr(response, "usage", None) or {}, provider="gemini")
    print(f"  actual reported input (no tools): {canonical.prompt_tokens}")

    for label, estimated in (("sync", sync_result.input_tokens), ("async", async_result.input_tokens)):
        if estimated and canonical.prompt_tokens:
            delta = abs(estimated - canonical.prompt_tokens) / canonical.prompt_tokens * 100
            verdict = "WITHIN 5%" if delta <= 5 else "OVER 5%"
            print(f"  {label:5s} vs actual: delta={delta:.1f}%  {verdict}")


def section_custom() -> None:
    hr("SECTION: custom tokenizer config modes")
    from tokenizer.custom_strategy import CustomStrategy

    modes = [
        ("no config (warn)", {}),
        ("tiktoken", {"type": "tiktoken", "encoding": "cl100k_base"}),
        ("heuristic override", {"type": "heuristic", "chars_per_token": 3.5}),
        ("hf (gated->fallback)", {"type": "hf", "model_id": "meta-llama/Llama-3.1-8B"}),
        ("native_api (deferred)", {"type": "native_api"}),
    ]
    for label, config in modes:
        strat = CustomStrategy(model="my-endpoint-model", tokenizer_config=config)
        result = strat.count_result(TEST_MESSAGES)
        info = strat.describe()
        print(f"  {label:24s} -> tokens={result.input_tokens} type={info.get('tokenizer_type')} "
              f"acc={info['accuracy_class']} method={result.method}")
        for w in result.warnings:
            print(f"    warning: {w}")


async def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]] or list(SECTIONS)
    unknown = [w for w in wanted if w not in SECTIONS]
    if unknown:
        print(f"Unknown sections: {unknown}. Available: {list(SECTIONS)}")
        return
    for section in wanted:
        if section == "routing":
            section_routing()
        elif section == "tiktoken":
            section_tiktoken()
        elif section == "heuristic":
            section_heuristic()
        elif section == "live":
            await section_live()
        elif section == "gemini":
            await section_gemini()
        elif section == "custom":
            section_custom()
    print()


if __name__ == "__main__":
    asyncio.run(main())
