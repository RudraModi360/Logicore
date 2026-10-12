"""
logicore.runtime.model_profile.py
Model profiling and tier resolution across heterogeneous providers.

Resolves how large a model's context window is and how aggressively the
runtime should mask/compress for it. Resolution priority:

1. Explicit user ``model_config`` dictionary
2. Known registry lookup (KNOWN_REGISTRY)
3. Name-based heuristic (size / vendor tags in the model name)
4. Sane fallback (standard tier, 64k window) with a runtime notice
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

ModelTier = Literal["small", "standard", "large"]

NOTICE_PREFIX = "[ModelConfig] Notice:"

DEFAULT_TIER: ModelTier = "standard"
DEFAULT_CONTEXT_LENGTH = 64000

TIER_RATIOS: Dict[str, Dict[str, float]] = {
    "small": {"masking_ratio": 0.55, "compression_ratio": 0.75},
    "standard": {"masking_ratio": 0.65, "compression_ratio": 0.80},
    "large": {"masking_ratio": 0.70, "compression_ratio": 0.85},
}

SMALL_CONTEXT_LENGTH = 32768
LARGE_CONTEXT_LENGTH = 200000

SMALL_NAME_TAGS = ("0.5b", "0.8b", "1b", "3b", "7b", "8b", "mini")
LARGE_NAME_TAGS = ("pro", "sonnet", "opus", "large")

TIER_ALIASES: Dict[str, ModelTier] = {
    "small": "small",
    "tiny": "small",
    "medium": "standard",
    "mid": "standard",
    "standard": "standard",
    "big": "large",
    "large": "large",
    "xl": "large",
}

KNOWN_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Small models (<32k or high-budget sensitivity)
    "gpt-4o-mini": {"context_length": 128000, "tier": "small"},
    "claude-3-haiku": {"context_length": 200000, "tier": "small"},
    "llama3:8b": {"context_length": 8192, "tier": "small"},
    "qwen2.5:7b": {"context_length": 32768, "tier": "small"},
    "qwen3.5:0.8b": {"context_length": 32768, "tier": "small"},

    # Standard models (32k - 128k)
    "gemma4:cloud": {"context_length": 128000, "tier": "standard"},
    "gpt-4o": {"context_length": 128000, "tier": "standard"},
    "nemotron-3-ultra:cloud": {"context_length": 128000, "tier": "standard"},

    # Large models (>128k)
    "claude-3-5-sonnet": {"context_length": 200000, "tier": "large"},
    "gemini-1.5-pro": {"context_length": 1000000, "tier": "large"},
    "gemini-1.5-flash": {"context_length": 1000000, "tier": "large"},
    "gemini-2.5-pro": {"context_length": 1000000, "tier": "large"},
    "gemini-2.5-flash": {"context_length": 1000000, "tier": "large"},
}


def normalize_tier(value: Any) -> ModelTier:
    if not isinstance(value, str):
        return DEFAULT_TIER
    return TIER_ALIASES.get(value.strip().lower(), DEFAULT_TIER)


def _name_keys(model_name: str) -> list:
    name = (model_name or "").strip().lower()
    if not name:
        return []
    tail = name.split("/")[-1]
    keys = [name, tail]
    if ":" in tail:
        keys.append(tail.split(":")[0])
    seen = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def _registry_lookup(model_name: str) -> Optional[Dict[str, Any]]:
    for key in _name_keys(model_name):
        meta = KNOWN_REGISTRY.get(key)
        if meta:
            return meta
    return None


def _window_from_name(model_name: str) -> Optional[int]:
    meta = _registry_lookup(model_name)
    if meta:
        return int(meta["context_length"])
    return None


def _heuristic_tier(model_name: str) -> Optional[ModelTier]:
    for key in _name_keys(model_name):
        if any(tag in key for tag in SMALL_NAME_TAGS):
            return "small"
        if any(tag in key for tag in LARGE_NAME_TAGS):
            return "large"
    return None


@dataclass
class ModelProfile:
    """Resolved capabilities and tuning ratios for one model."""

    tier: ModelTier = DEFAULT_TIER
    context_length: int = DEFAULT_CONTEXT_LENGTH
    masking_ratio: float = 0.60
    compression_ratio: float = 0.80
    model_name: str = ""
    source: str = "fallback"
    model_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def _build(
        cls,
        tier: Any,
        context_length: int,
        model_name: str = "",
        source: str = "fallback",
        model_config: Optional[Dict[str, Any]] = None,
    ) -> "ModelProfile":
        tier = normalize_tier(tier)
        ratios = TIER_RATIOS[tier]
        return cls(
            tier=tier,
            context_length=int(context_length),
            masking_ratio=ratios["masking_ratio"],
            compression_ratio=ratios["compression_ratio"],
            model_name=model_name,
            source=source,
            model_config=dict(model_config or {}),
        )

    @classmethod
    def resolve(
        cls,
        model_name: str,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> "ModelProfile":
        """
        Resolve model profile with priority:
        1. Explicit user model_config dictionary
        2. Known registry lookup
        3. Name-based heuristic
        4. Sane fallback (standard tier, 64k window) with runtime notification
        """
        name = (model_name or "").strip()

        if model_config and isinstance(model_config, dict) and model_config:
            tier = normalize_tier(model_config.get("model_tier", DEFAULT_TIER))
            context_length = model_config.get("context_length")
            if not isinstance(context_length, int) or context_length <= 0:
                context_length = _window_from_name(name) or DEFAULT_CONTEXT_LENGTH
            profile = cls._build(
                tier, context_length, model_name=name,
                source="config", model_config=model_config,
            )
            override_mask = model_config.get("masking_ratio")
            override_comp = model_config.get("compression_ratio")
            if isinstance(override_mask, (int, float)) and 0.0 < float(override_mask) < 1.0:
                profile.masking_ratio = float(override_mask)
            if isinstance(override_comp, (int, float)) and 0.0 < float(override_comp) < 1.0:
                profile.compression_ratio = float(override_comp)
            return profile

        meta = _registry_lookup(name)
        if meta:
            return cls._build(
                meta["tier"], int(meta["context_length"]),
                model_name=name, source="registry",
            )

        tier = _heuristic_tier(name)
        if tier == "small":
            return cls._build(
                tier, SMALL_CONTEXT_LENGTH, model_name=name, source="heuristic",
            )
        if tier == "large":
            return cls._build(
                tier, LARGE_CONTEXT_LENGTH, model_name=name, source="heuristic",
            )

        print(
            f"{NOTICE_PREFIX} No explicit model_config provided for '{name}'. "
            f"Defaulting to standard tier ({DEFAULT_CONTEXT_LENGTH // 1000}k context). "
            f"Pass model_config={{'context_length': ..., 'model_tier': ...}} for custom optimization."
        )
        return cls._build(
            DEFAULT_TIER, DEFAULT_CONTEXT_LENGTH, model_name=name, source="fallback",
        )

    @classmethod
    def from_tier(
        cls,
        tier: Any,
        context_length: Optional[int] = None,
        model_name: str = "",
    ) -> "ModelProfile":
        tier = normalize_tier(tier)
        if context_length is None:
            meta = _registry_lookup(model_name)
            context_length = int(meta["context_length"]) if meta else (
                SMALL_CONTEXT_LENGTH if tier == "small"
                else LARGE_CONTEXT_LENGTH if tier == "large"
                else DEFAULT_CONTEXT_LENGTH
            )
        return cls._build(tier, context_length, model_name=model_name, source="config")

    def compression_threshold_tokens(self) -> int:
        """Tokens at which history summarization should fire for this window."""
        return max(1000, int(self.context_length * self.compression_ratio))

    def protection_threshold_tokens(self) -> int:
        """
        Recent tool-output budget kept verbatim by the masking engine; older
        prunable outputs beyond this are maskable. Derived from the window so
        small models mask sooner in absolute terms.
        """
        return max(1000, int(self.context_length * (1.0 - self.masking_ratio)))

    def apply_to_context(self, context_config: Any) -> Any:
        """Write this profile into a ContextConfig (mutates and returns it)."""
        context_config.max_context_tokens = self.context_length
        context_config.compression_threshold_ratio = self.compression_ratio
        context_config.compression_threshold_tokens = self.compression_threshold_tokens()
        context_config.protection_threshold_tokens = self.protection_threshold_tokens()
        return context_config

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "tier": self.tier,
            "context_length": self.context_length,
            "masking_ratio": self.masking_ratio,
            "compression_ratio": self.compression_ratio,
            "compression_threshold_tokens": self.compression_threshold_tokens(),
            "protection_threshold_tokens": self.protection_threshold_tokens(),
            "source": self.source,
            "model_config": dict(self.model_config),
        }
