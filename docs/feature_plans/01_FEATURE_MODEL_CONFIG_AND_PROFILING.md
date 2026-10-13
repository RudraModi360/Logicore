# Feature Plan 01: Model Config & Context Profiling (Part A)

## Overview & Objective
Enable Logicore agents to adapt dynamically to any model (including arbitrary Hugging Face / Ollama models like Qwen 3.5 0.8B) by introducing an optional `model_config` dictionary on [`Agent`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py).

This eliminates hardcoded context assumptions (which previously defaulted everything to 128,000 tokens) and sets appropriate masking and compression ratios automatically based on the model tier.

---

## Status & Tracking
- **Status:** `SHIPPED (merged to framework, commit 03a1dee)`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - `logicore/runtime/model_profile.py` [NEW]
  - `logicore/agent/base.py` [MODIFY]
  - `logicore/runtime/config.py` [MODIFY]
  - `tests/test_model_profile.py` [NEW]
  - `experiments/prototype_agent.py` [MODIFY - strategy wired in + profile tracing]

---

## Detailed Design & Code Changes

### 1. [NEW] `logicore/runtime/model_profile.py`
Create a centralized profile abstraction that resolves model capabilities and context windows:

```python
"""
logicore/runtime/model_profile.py
Model profiling and tier resolution across heterogeneous providers.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, Literal
import logging

logger = logging.getLogger(__name__)

ModelTier = Literal["small", "standard", "large"]

# Known registry for common models (fast lookup)
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
}


@dataclass
class ModelProfile:
    tier: ModelTier = "standard"
    context_length: int = 64000
    masking_ratio: float = 0.60
    compression_ratio: float = 0.80

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
        # 1. Explicit user override
        if model_config and isinstance(model_config, dict):
            tier = model_config.get("model_tier", "standard")
            context_len = model_config.get("context_length", 64000)
            return cls(
                tier=tier,
                context_length=context_len,
                masking_ratio=0.55 if tier == "small" else (0.65 if tier == "standard" else 0.70),
                compression_ratio=0.75 if tier == "small" else (0.80 if tier == "standard" else 0.85),
            )

        clean_name = model_name.lower().split("/")[-1].split(":")[0]

        # 2. Known registry lookup
        if model_name.lower() in KNOWN_REGISTRY:
            meta = KNOWN_REGISTRY[model_name.lower()]
            return cls(tier=meta["tier"], context_length=meta["context_length"])
        if clean_name in KNOWN_REGISTRY:
            meta = KNOWN_REGISTRY[clean_name]
            return cls(tier=meta["tier"], context_length=meta["context_length"])

        # 3. Name-based heuristics
        if any(tag in clean_name for tag in ["0.5b", "0.8b", "1b", "3b", "7b", "8b", "mini"]):
            return cls(tier="small", context_length=32768, masking_ratio=0.55, compression_ratio=0.75)
        if any(tag in clean_name for tag in ["pro", "sonnet", "opus", "large"]):
            return cls(tier="large", context_length=200000, masking_ratio=0.70, compression_ratio=0.85)

        # 4. Default fallback with notification
        print(
            f"[ModelConfig] Notice: No explicit model_config provided for '{model_name}'. "
            f"Defaulting to standard tier (64k context). "
            f"Pass model_config={{'context_length': ..., 'model_tier': ...}} for custom optimization."
        )
        return cls(tier="standard", context_length=64000, masking_ratio=0.60, compression_ratio=0.80)
```

---

### 2. [MODIFY] `logicore/agent/base.py`
In [`Agent.__init__`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/agent/base.py#L120):
- Accept `model_config: Optional[Dict[str, Any]] = None`.
- Initialize `self.model_profile = ModelProfile.resolve(self.model, model_config)`.
- Pass `model_profile.context_length` into `RuntimeConfig.context.max_context_tokens`.
- Pass `model_profile.masking_ratio` and `model_profile.compression_ratio` into `RuntimeConfig.context`.

---

### 3. [MODIFY] `logicore/runtime/config.py`
In `ContextConfig` ([`logicore/runtime/config.py:109`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/config.py#L109)):
- Allow dynamic overrides for `max_context_tokens`, `compression_threshold_ratio`, and `protection_threshold_tokens` based on the active `ModelProfile`.

---

## Verification Plan

### Automated Unit Test: `tests/test_model_profile.py`
```python
import pytest
from logicore.runtime.model_profile import ModelProfile

def test_explicit_model_config():
    profile = ModelProfile.resolve("ollama/custom-model", model_config={"context_length": 16384, "model_tier": "small"})
    assert profile.tier == "small"
    assert profile.context_length == 16384
    assert profile.compression_ratio == 0.75

def test_default_fallback_with_notice(capsys):
    profile = ModelProfile.resolve("ollama/unknown-custom-model")
    captured = capsys.readouterr()
    assert "[ModelConfig] Notice:" in captured.out
    assert profile.tier == "standard"
    assert profile.context_length == 64000
```

### Manual Verification
Run the test command:
```powershell
pytest tests/test_model_profile.py -v
```

---

## Acceptance Criteria
- [x] `Agent(model="custom", model_config={"context_length": 16000, "model_tier": "small"})` stores 16k context window in runtime config.
- [x] Calling `Agent(model="custom")` outputs the friendly notice without crashing.
- [x] Automated tests in `tests/test_model_profile.py` pass 100%.

---

## Execution Results (for PO sign-off)

Executed per the PO's class-based methodology: the profiling strategy lives in
one class (`ModelProfile`) with methods (`resolve`, `apply_to_context`,
`compression_threshold_tokens`, `protection_threshold_tokens`, `to_dict`), is
wired into the main framework AND the prototype, and emits trace records so the
strategy's real behavior can be analyzed and tuned from feedback.

**Verification**
- `pytest tests/test_model_profile.py -v` -> 12/12 passed (both plan tests plus
  registry/heuristic/alias/tuning-override/apply/Agent-acceptance coverage).
- PO playground: `python experiments/model_profile_playground.py` - live
  sandbox to play with `model_config` (tier/window/ratio overrides), see the
  ContextConfig before->after diff, `--demo` all resolution sources,
  `--agent` real Agent wiring, or interactive mode.
- `python experiments/plan00_smoke_check.py` -> 29/29 passed (B6/B7 check the
  prototype wiring + profile tracing).
- Live run `python experiments/prototype_agent.py --provider ollama --model
  gemma4:cloud --turns "hi"` -> `Profile | tier=standard window=128000
  source=registry masking=0.65 compression=0.80`.
- Live run with overrides `--model-tier small --context-length 16000` ->
  `Profile | tier=small window=16000 source=config ...`.
- Full `pytest tests -q`: 301 passed; the 8 failures reproduce identically on
  pristine HEAD (broken local ollama model `qwen3:0.6b`, Groq 403, flaky perf
  test, stale fakes) - unrelated to this plan.

**Trace records for strategy analysis** (every prototype run,
`debug/traces/prototype/*.jsonl`):
- `profile_resolved` (seq 1): full profile payload - model, tier,
  context_length, masking_ratio, compression_ratio, derived thresholds, source
  (`config|registry|heuristic|fallback`) and the raw model_config.
- `session_start`: embeds the same profile.
- `llm_request`: adds `context_limit` and `context_pct` so the PO can see how
  close real traffic gets to the configured window per strategy.

**Design refinements over the plan text (intentional):**
1. Tier ratios come from one `TIER_RATIOS` table applied on every path (the
   plan's registry path would have silently kept default ratios).
2. Registry lookup also matches provider-prefixed and tag-suffixed names
   (`ollama/qwen3.5:0.8b`), not just exact/clean names.
3. `model_config` with only `model_tier` (no `context_length`) borrows the
   window from the registry/heuristic instead of hard-64k.
4. Optional `masking_ratio`/`compression_ratio` keys in `model_config` allow
   PO-driven A/B tuning of the strategy without code changes.
5. `compression_threshold_tokens` is derived from the window (kills the
   absolute-100k fallback for small windows - the Plan 00 finding) and
   `protection_threshold_tokens = context_length * (1 - masking_ratio)`
   (recent verbatim budget; scales so small models mask sooner in absolute
   terms).
6. `RuntimeConfig.model_profile` + profile-first `get_model_context_window`
   so TokenBudget/masking/compression all see the resolved window; agents
   without a profile keep the old estimator behavior.
7. `gemini-2.5-flash`/`gemini-2.5-pro` added to the registry (1M, large).

**Merged into the framework (commit `03a1dee`, branch `opencode_mem_fix`):**
- `ModelProfile` / `ModelTier` are public runtime exports
  (`from logicore.runtime import ModelProfile`).
- `BasicAgent(...)` / `create_agent(...)` forward `model_config` to `Agent`
  (previously swallowed by `**kwargs`).
- `RuntimeConfig.to_dict()` serializes the active profile (+ `max_context_tokens`)
  for observability.
- Files: `logicore/runtime/model_profile.py`, `runtime/__init__.py`,
  `runtime/config.py`, `agent/base.py`, `agent/variants/basic.py`,
  `tests/test_model_profile.py` (14/14), `experiments/model_profile_playground.py`.

**Full modular rollout (commit `b1c4f82`):** `model_config` is now available
everywhere, per the PO's modular-framework directive:
- **Provider level** — `LLMProvider.setup_model_profile()`; every concrete
  provider (ollama/gemini/groq/openai/custom/azure) accepts `model_config`
  at construction and carries its own `model_profile`, so a bare LLM
  connection works standalone. `CustomProvider` folds its legacy
  `context_window` into the profile. `get_metadata()` includes the profile.
- **Factory** — `create_provider(..., model_config=...)` forwards to all
  branches (incl. `register_provider` customs).
- **Agent** — reuses the provider's profile when no override is given;
  agent-level `model_config` wins and is synced back onto the provider.
- **All variants** — `BasicAgent`/`create_agent` (kwargs), `CopilotAgent`,
  `SmartAgent`, `MCPAgent` all accept `model_config`.
- Tests: `test_model_profile.py` 22/22 (provider standalone, factory,
  legacy window fold-in, agent reuse/override/sync, all variants).
