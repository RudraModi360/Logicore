"""
Cost estimation for Logicore telemetry.

Multi-tier pricing resolution:
1. Subscription-included routes (e.g. Codex) → $0
2. OpenRouter live models API (cached 1hr)
3. Custom endpoint /models API (per-token pricing)
4. Official docs snapshot (hardcoded per-model pricing)
5. Unknown → status "unknown", no dollar amount

All money uses Decimal for precision. Local/Ollama models return
status "unknown" with no cost — all other telemetry fields still tracked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_ONE_MILLION = Decimal("1000000")

CostStatus = Literal["actual", "estimated", "included", "unknown"]
CostSource = Literal[
    "provider_cost_api",
    "provider_generation_api",
    "provider_models_api",
    "official_docs_snapshot",
    "user_override",
    "custom_contract",
    "none",
]


@dataclass(frozen=True)
class BillingRoute:
    provider: str
    model: str
    base_url: str = ""
    billing_mode: str = "unknown"


@dataclass(frozen=True)
class PricingEntry:
    input_cost_per_million: Optional[Decimal] = None
    output_cost_per_million: Optional[Decimal] = None
    cache_read_cost_per_million: Optional[Decimal] = None
    cache_write_cost_per_million: Optional[Decimal] = None
    request_cost: Optional[Decimal] = None
    source: CostSource = "none"
    source_url: Optional[str] = None
    pricing_version: Optional[str] = None


@dataclass(frozen=True)
class CostResult:
    amount_usd: Optional[Decimal]
    status: CostStatus
    source: CostSource
    label: str
    pricing_version: Optional[str] = None


def _base_url_hostname(base_url: str) -> str:
    try:
        parsed = urlparse(base_url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _base_url_host_matches(base_url: str, domain: str) -> bool:
    hostname = _base_url_hostname(base_url)
    if not hostname:
        return False
    domain = domain.strip().lower().rstrip(".")
    return hostname == domain or hostname.endswith("." + domain)


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def resolve_billing_route(
    model_name: str,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
) -> BillingRoute:
    provider_name = (provider or "").strip().lower()
    base = (base_url or "").strip().lower()
    model = (model_name or "").strip()

    if not provider_name and "/" in model:
        inferred_provider, bare_model = model.split("/", 1)
        if inferred_provider in {"anthropic", "openai", "google"}:
            provider_name = inferred_provider
            model = bare_model

    if provider_name == "openai-codex":
        return BillingRoute(provider="openai-codex", model=model,
                            base_url=base_url or "", billing_mode="subscription_included")

    if provider_name == "openrouter" or _base_url_host_matches(base_url or "", "openrouter.ai"):
        return BillingRoute(provider="openrouter", model=model,
                            base_url=base_url or "", billing_mode="official_models_api")

    if provider_name in {"minimax", "minimax-cn"}:
        return BillingRoute(provider=provider_name, model=model.split("/")[-1],
                            base_url=base_url or "", billing_mode="official_docs_snapshot")

    if provider_name in {"anthropic", "openai", "google", "deepseek"}:
        return BillingRoute(provider=provider_name, model=model.split("/")[-1],
                            base_url=base_url or "", billing_mode="official_docs_snapshot")

    if provider_name in {"custom", "local"} or ("localhost" in base):
        return BillingRoute(provider=provider_name or "custom", model=model,
                            base_url=base_url or "", billing_mode="unknown")

    return BillingRoute(provider=provider_name or "unknown",
                        model=model.split("/")[-1] if model else "",
                        base_url=base_url or "", billing_mode="unknown")


_OFFICIAL_DOCS_PRICING: Dict[tuple, PricingEntry] = {
    ("anthropic", "claude-opus-4-8"): PricingEntry(
        input_cost_per_million=Decimal("5.00"), output_cost_per_million=Decimal("25.00"),
        cache_read_cost_per_million=Decimal("0.50"), cache_write_cost_per_million=Decimal("6.25"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2026-05",
    ),
    ("anthropic", "claude-sonnet-4-8"): PricingEntry(
        input_cost_per_million=Decimal("3.00"), output_cost_per_million=Decimal("15.00"),
        cache_read_cost_per_million=Decimal("0.30"), cache_write_cost_per_million=Decimal("3.75"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2026-05",
    ),
    ("anthropic", "claude-opus-4-5"): PricingEntry(
        input_cost_per_million=Decimal("15.00"), output_cost_per_million=Decimal("75.00"),
        cache_read_cost_per_million=Decimal("1.50"), cache_write_cost_per_million=Decimal("18.75"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2025-02",
    ),
    ("anthropic", "claude-sonnet-4-5"): PricingEntry(
        input_cost_per_million=Decimal("3.00"), output_cost_per_million=Decimal("15.00"),
        cache_read_cost_per_million=Decimal("0.30"), cache_write_cost_per_million=Decimal("3.75"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2025-02",
    ),
    ("anthropic", "claude-haiku-4-5"): PricingEntry(
        input_cost_per_million=Decimal("0.80"), output_cost_per_million=Decimal("4.00"),
        cache_read_cost_per_million=Decimal("0.08"), cache_write_cost_per_million=Decimal("1.00"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2025-02",
    ),
    ("anthropic", "claude-3-5-sonnet"): PricingEntry(
        input_cost_per_million=Decimal("3.00"), output_cost_per_million=Decimal("15.00"),
        cache_read_cost_per_million=Decimal("0.30"), cache_write_cost_per_million=Decimal("3.75"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2024-10",
    ),
    ("anthropic", "claude-3-5-haiku"): PricingEntry(
        input_cost_per_million=Decimal("0.80"), output_cost_per_million=Decimal("4.00"),
        cache_read_cost_per_million=Decimal("0.08"), cache_write_cost_per_million=Decimal("1.00"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2024-10",
    ),
    ("anthropic", "claude-3-opus"): PricingEntry(
        input_cost_per_million=Decimal("15.00"), output_cost_per_million=Decimal("75.00"),
        cache_read_cost_per_million=Decimal("1.50"), cache_write_cost_per_million=Decimal("18.75"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2024-01",
    ),
    ("anthropic", "claude-3-haiku"): PricingEntry(
        input_cost_per_million=Decimal("0.25"), output_cost_per_million=Decimal("1.25"),
        cache_read_cost_per_million=Decimal("0.03"), cache_write_cost_per_million=Decimal("0.30"),
        source="official_docs_snapshot", pricing_version="anthropic-pricing-2024-01",
    ),
    ("openai", "gpt-4o"): PricingEntry(
        input_cost_per_million=Decimal("2.50"), output_cost_per_million=Decimal("10.00"),
        cache_read_cost_per_million=Decimal("1.25"), cache_write_cost_per_million=Decimal("2.50"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "gpt-4o-mini"): PricingEntry(
        input_cost_per_million=Decimal("0.15"), output_cost_per_million=Decimal("0.60"),
        cache_read_cost_per_million=Decimal("0.075"), cache_write_cost_per_million=Decimal("0.15"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "gpt-4.1"): PricingEntry(
        input_cost_per_million=Decimal("2.00"), output_cost_per_million=Decimal("8.00"),
        cache_read_cost_per_million=Decimal("0.50"), cache_write_cost_per_million=Decimal("2.00"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "gpt-4.1-mini"): PricingEntry(
        input_cost_per_million=Decimal("0.40"), output_cost_per_million=Decimal("1.60"),
        cache_read_cost_per_million=Decimal("0.10"), cache_write_cost_per_million=Decimal("0.40"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "gpt-4.1-nano"): PricingEntry(
        input_cost_per_million=Decimal("0.10"), output_cost_per_million=Decimal("0.40"),
        cache_read_cost_per_million=Decimal("0.025"), cache_write_cost_per_million=Decimal("0.10"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "o3"): PricingEntry(
        input_cost_per_million=Decimal("10.00"), output_cost_per_million=Decimal("40.00"),
        cache_read_cost_per_million=Decimal("2.50"), cache_write_cost_per_million=Decimal("10.00"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "o3-mini"): PricingEntry(
        input_cost_per_million=Decimal("1.10"), output_cost_per_million=Decimal("4.40"),
        cache_read_cost_per_million=Decimal("0.275"), cache_write_cost_per_million=Decimal("1.10"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("openai", "o4-mini"): PricingEntry(
        input_cost_per_million=Decimal("1.10"), output_cost_per_million=Decimal("4.40"),
        cache_read_cost_per_million=Decimal("0.275"), cache_write_cost_per_million=Decimal("1.10"),
        source="official_docs_snapshot", pricing_version="openai-pricing-2025-06",
    ),
    ("google", "gemini-2.5-pro"): PricingEntry(
        input_cost_per_million=Decimal("1.25"), output_cost_per_million=Decimal("10.00"),
        cache_read_cost_per_million=Decimal("0.315"), cache_write_cost_per_million=Decimal("1.25"),
        source="official_docs_snapshot", pricing_version="google-pricing-2025-06",
    ),
    ("google", "gemini-2.5-flash"): PricingEntry(
        input_cost_per_million=Decimal("0.15"), output_cost_per_million=Decimal("0.60"),
        cache_read_cost_per_million=Decimal("0.0375"), cache_write_cost_per_million=Decimal("0.15"),
        source="official_docs_snapshot", pricing_version="google-pricing-2025-06",
    ),
    ("google", "gemini-2.0-flash"): PricingEntry(
        input_cost_per_million=Decimal("0.10"), output_cost_per_million=Decimal("0.40"),
        cache_read_cost_per_million=Decimal("0.025"), cache_write_cost_per_million=Decimal("0.10"),
        source="official_docs_snapshot", pricing_version="google-pricing-2025-06",
    ),
    ("deepseek", "deepseek-chat"): PricingEntry(
        input_cost_per_million=Decimal("0.14"), output_cost_per_million=Decimal("0.28"),
        cache_read_cost_per_million=Decimal("0.014"), cache_write_cost_per_million=Decimal("0.14"),
        source="official_docs_snapshot", pricing_version="deepseek-pricing-2025-06",
    ),
    ("deepseek", "deepseek-reasoner"): PricingEntry(
        input_cost_per_million=Decimal("0.55"), output_cost_per_million=Decimal("2.19"),
        cache_read_cost_per_million=Decimal("0.055"), cache_write_cost_per_million=Decimal("0.55"),
        source="official_docs_snapshot", pricing_version="deepseek-pricing-2025-06",
    ),
}

_MODEL_ALIASES: Dict[str, str] = {
    "claude-opus-4-20250514": "claude-opus-4-8",
    "claude-sonnet-4-20250514": "claude-sonnet-4-8",
}


def _lookup_official_docs_pricing(route: BillingRoute) -> Optional[PricingEntry]:
    model = route.model
    provider = route.provider

    alias = _MODEL_ALIASES.get(model)
    if alias:
        model = alias

    entry = _OFFICIAL_DOCS_PRICING.get((provider, model))
    if entry:
        return entry

    normalized = model.replace(".", "-")
    if normalized != model:
        entry = _OFFICIAL_DOCS_PRICING.get((provider, normalized))
        if entry:
            return entry

    return None


_OPENROUTER_CACHE: Dict[str, Dict[str, Any]] = {}
_OPENROUTER_CACHE_TIME: float = 0
_OPENROUTER_CACHE_TTL: float = 3600


def _fetch_openrouter_models() -> Dict[str, Dict[str, Any]]:
    global _OPENROUTER_CACHE, _OPENROUTER_CACHE_TIME
    if _OPENROUTER_CACHE and (time.time() - _OPENROUTER_CACHE_TIME) < _OPENROUTER_CACHE_TTL:
        return _OPENROUTER_CACHE
    try:
        import requests
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cache = {}
        for m in data.get("data", []):
            mid = m.get("id", "")
            cache[mid] = {"pricing": m.get("pricing", {}), "context_length": m.get("context_length", 128000)}
            canonical = m.get("canonical_slug", "")
            if canonical and canonical != mid:
                cache[canonical] = cache[mid]
        _OPENROUTER_CACHE = cache
        _OPENROUTER_CACHE_TIME = time.time()
        return cache
    except Exception as e:
        logger.debug(f"Failed to fetch OpenRouter models: {e}")
        return _OPENROUTER_CACHE or {}


def _openrouter_pricing_entry(route: BillingRoute) -> Optional[PricingEntry]:
    models = _fetch_openrouter_models()
    if route.model not in models:
        return None
    pricing = models[route.model].get("pricing", {})
    prompt = _to_decimal(pricing.get("prompt"))
    completion = _to_decimal(pricing.get("completion"))
    cache_read = _to_decimal(pricing.get("cache_read") or pricing.get("cached_prompt"))
    cache_write = _to_decimal(pricing.get("cache_write") or pricing.get("cache_creation"))
    if prompt is None and completion is None:
        return None
    return PricingEntry(
        input_cost_per_million=prompt * _ONE_MILLION if prompt else None,
        output_cost_per_million=completion * _ONE_MILLION if completion else None,
        cache_read_cost_per_million=cache_read * _ONE_MILLION if cache_read else None,
        cache_write_cost_per_million=cache_write * _ONE_MILLION if cache_write else None,
        source="provider_models_api",
        source_url="https://openrouter.ai/api/v1/models",
        pricing_version="openrouter-models-api",
    )


def _fetch_endpoint_model_metadata(base_url: str, api_key: str = "") -> Dict[str, Dict[str, Any]]:
    if not base_url:
        return {}
    try:
        import requests
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        url = base_url.rstrip("/") + "/models"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cache = {}
        for m in data.get("data", []):
            mid = m.get("id", "")
            pricing = m.get("pricing", {})
            if pricing:
                cache[mid] = {"pricing": pricing}
        return cache
    except Exception as e:
        logger.debug(f"Failed to fetch models from {base_url}: {e}")
        return {}


def _pricing_entry_from_metadata(
    metadata: Dict[str, Dict[str, Any]],
    model_id: str,
    *,
    source_url: str,
    pricing_version: str,
) -> Optional[PricingEntry]:
    if model_id not in metadata:
        return None
    pricing = metadata[model_id].get("pricing", {})
    prompt = _to_decimal(pricing.get("prompt"))
    completion = _to_decimal(pricing.get("completion"))
    cache_read = _to_decimal(pricing.get("cache_read") or pricing.get("cached_prompt") or pricing.get("input_cache_read"))
    cache_write = _to_decimal(pricing.get("cache_write") or pricing.get("cache_creation") or pricing.get("input_cache_write"))
    if prompt is None and completion is None:
        return None
    return PricingEntry(
        input_cost_per_million=prompt * _ONE_MILLION if prompt else None,
        output_cost_per_million=completion * _ONE_MILLION if completion else None,
        cache_read_cost_per_million=cache_read * _ONE_MILLION if cache_read else None,
        cache_write_cost_per_million=cache_write * _ONE_MILLION if cache_write else None,
        source="provider_models_api",
        source_url=source_url,
        pricing_version=pricing_version,
    )


def get_pricing_entry(
    model_name: str,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[PricingEntry]:
    route = resolve_billing_route(model_name, provider=provider, base_url=base_url)

    if route.billing_mode == "subscription_included":
        return PricingEntry(
            input_cost_per_million=_ZERO, output_cost_per_million=_ZERO,
            cache_read_cost_per_million=_ZERO, cache_write_cost_per_million=_ZERO,
            source="none", pricing_version="included-route",
        )

    if route.provider == "openrouter":
        entry = _openrouter_pricing_entry(route)
        if entry:
            return entry

    if route.base_url:
        entry = _pricing_entry_from_metadata(
            _fetch_endpoint_model_metadata(route.base_url, api_key=api_key or ""),
            route.model,
            source_url=f"{route.base_url.rstrip('/')}/models",
            pricing_version="openai-compatible-models-api",
        )
        if entry:
            return entry

    return _lookup_official_docs_pricing(route)


def estimate_usage_cost(
    model_name: str,
    usage: Any,
    *,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CostResult:
    from logicore.telemetry.canonical import CanonicalUsage

    route = resolve_billing_route(model_name, provider=provider, base_url=base_url)

    if route.billing_mode == "subscription_included":
        return CostResult(amount_usd=_ZERO, status="included", source="none", label="included")

    entry = get_pricing_entry(model_name, provider=provider, base_url=base_url, api_key=api_key)
    if not entry:
        return CostResult(amount_usd=None, status="unknown", source="none", label="n/a")

    if isinstance(usage, CanonicalUsage):
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = usage.cache_read_tokens
        cache_write = usage.cache_write_tokens
        request_count = usage.request_count
    else:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_tokens", 0) or 0
        cache_write = getattr(usage, "cache_write_tokens", 0) or 0
        request_count = getattr(usage, "request_count", 1) or 1

    if input_tokens and entry.input_cost_per_million is None:
        return CostResult(amount_usd=None, status="unknown", source=entry.source, label="n/a")
    if output_tokens and entry.output_cost_per_million is None:
        return CostResult(amount_usd=None, status="unknown", source=entry.source, label="n/a")
    if cache_read and entry.cache_read_cost_per_million is None:
        return CostResult(amount_usd=None, status="unknown", source=entry.source, label="n/a")
    if cache_write and entry.cache_write_cost_per_million is None:
        return CostResult(amount_usd=None, status="unknown", source=entry.source, label="n/a")

    amount = _ZERO
    if entry.input_cost_per_million is not None:
        amount += Decimal(input_tokens) * entry.input_cost_per_million / _ONE_MILLION
    if entry.output_cost_per_million is not None:
        amount += Decimal(output_tokens) * entry.output_cost_per_million / _ONE_MILLION
    if entry.cache_read_cost_per_million is not None:
        amount += Decimal(cache_read) * entry.cache_read_cost_per_million / _ONE_MILLION
    if entry.cache_write_cost_per_million is not None:
        amount += Decimal(cache_write) * entry.cache_write_cost_per_million / _ONE_MILLION
    if entry.request_cost is not None and request_count:
        amount += Decimal(request_count) * entry.request_cost

    status: CostStatus = "estimated"
    label = f"~${amount:.4f}"
    if entry.source == "none" and amount == _ZERO:
        status = "included"
        label = "included"

    return CostResult(
        amount_usd=amount, status=status, source=entry.source,
        label=label, pricing_version=entry.pricing_version,
    )


def has_known_pricing(model_name: str, provider: Optional[str] = None, base_url: Optional[str] = None) -> bool:
    route = resolve_billing_route(model_name, provider=provider, base_url=base_url)
    if route.billing_mode == "subscription_included":
        return True
    entry = get_pricing_entry(model_name, provider=provider, base_url=base_url)
    return entry is not None
