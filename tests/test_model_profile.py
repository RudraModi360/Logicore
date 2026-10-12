"""
tests/test_model_profile.py
Plan 01: Model Config & Context Profiling verification.
"""

import pytest

from logicore.runtime.model_profile import (
    ModelProfile,
    NOTICE_PREFIX,
    TIER_RATIOS,
)
from logicore.runtime.config import ContextConfig, RuntimeConfig


def test_explicit_model_config():
    profile = ModelProfile.resolve(
        "ollama/custom-model",
        model_config={"context_length": 16384, "model_tier": "small"},
    )
    assert profile.tier == "small"
    assert profile.context_length == 16384
    assert profile.compression_ratio == 0.75
    assert profile.masking_ratio == 0.55
    assert profile.source == "config"


def test_default_fallback_with_notice(capsys):
    profile = ModelProfile.resolve("ollama/unknown-custom-model")
    captured = capsys.readouterr()
    assert NOTICE_PREFIX in captured.out
    assert profile.tier == "standard"
    assert profile.context_length == 64000
    assert profile.source == "fallback"


def test_registry_lookup():
    profile = ModelProfile.resolve("llama3:8b")
    assert profile.tier == "small"
    assert profile.context_length == 8192
    assert profile.source == "registry"

    profile = ModelProfile.resolve("gemma4:cloud")
    assert profile.tier == "standard"
    assert profile.context_length == 128000
    assert profile.source == "registry"

    profile = ModelProfile.resolve("ollama/qwen3.5:0.8b")
    assert profile.tier == "small"
    assert profile.context_length == 32768
    assert profile.source == "registry"


def test_heuristic_small_and_large():
    profile = ModelProfile.resolve("ollama/my-custom-7b-instruct")
    assert profile.tier == "small"
    assert profile.context_length == 32768
    assert profile.source == "heuristic"

    profile = ModelProfile.resolve("my-local-claude-sonnet-fake")
    assert profile.tier == "large"
    assert profile.context_length == 200000
    assert profile.source == "heuristic"


def test_medium_alias_normalizes_to_standard():
    profile = ModelProfile.resolve(
        "some-model", model_config={"model_tier": "medium", "context_length": 40000}
    )
    assert profile.tier == "standard"
    assert profile.masking_ratio == TIER_RATIOS["standard"]["masking_ratio"]


def test_ratio_overrides_for_tuning():
    profile = ModelProfile.resolve(
        "gemma4:cloud",
        model_config={"masking_ratio": 0.5, "compression_ratio": 0.7},
    )
    assert profile.source == "config"
    assert profile.context_length == 128000
    assert profile.masking_ratio == 0.5
    assert profile.compression_ratio == 0.7


def test_apply_to_context_config():
    profile = ModelProfile.resolve(
        "custom", model_config={"context_length": 16000, "model_tier": "small"}
    )
    ctx = profile.apply_to_context(ContextConfig())
    assert ctx.max_context_tokens == 16000
    assert ctx.compression_threshold_ratio == 0.75
    assert ctx.compression_threshold_tokens == 12000
    assert ctx.protection_threshold_tokens == int(16000 * (1 - 0.55))


def test_runtime_config_apply_and_window_lookup():
    config = RuntimeConfig()
    profile = ModelProfile.resolve("llama3:8b")
    config.apply_model_profile(profile)
    assert config.model_profile is profile
    assert config.context.max_context_tokens == 8192
    assert config.get_model_context_window("llama3:8b") == 8192
    assert config.get_compression_threshold_for_model("llama3:8b") == int(
        8192 * profile.compression_ratio
    )


def test_runtime_config_without_profile_uses_estimator():
    config = RuntimeConfig()
    assert config.model_profile is None
    window = config.get_model_context_window("gpt-4o")
    assert window == 128000


def test_agent_stores_context_window_in_runtime_config():
    from logicore import Agent

    agent = Agent(
        model="custom",
        model_config={"context_length": 16000, "model_tier": "small"},
    )
    assert agent.model_profile.tier == "small"
    assert agent.model_profile.context_length == 16000
    assert agent.runtime_config.context.max_context_tokens == 16000
    assert agent.runtime_config.context.compression_threshold_ratio == 0.75
    assert agent.runtime_config.model_profile is agent.model_profile


def test_agent_fallback_notice_without_crash(capsys):
    from logicore import Agent

    agent = Agent(model="custom")
    captured = capsys.readouterr()
    assert NOTICE_PREFIX in captured.out
    assert agent.model_profile.tier == "standard"
    assert agent.model_profile.context_length == 64000
    assert agent.runtime_config.context.max_context_tokens == 64000


def test_to_dict_for_tracing():
    payload = ModelProfile.resolve("gemma4:cloud").to_dict()
    assert payload["tier"] == "standard"
    assert payload["context_length"] == 128000
    assert payload["source"] == "registry"
    assert payload["compression_threshold_tokens"] == int(128000 * 0.80)
    assert payload["protection_threshold_tokens"] == int(128000 * 0.35)


def test_create_agent_forwards_model_config():
    from logicore import create_agent

    agent = create_agent(
        model="custom",
        model_config={"context_length": 16000, "model_tier": "small"},
    )
    inner = agent._agent
    assert inner.model_profile.context_length == 16000
    assert inner.model_profile.tier == "small"
    assert inner.runtime_config.context.max_context_tokens == 16000


def test_runtime_config_serialization_includes_profile():
    config = RuntimeConfig()
    assert config.to_dict()["model_profile"] is None
    config.apply_model_profile(ModelProfile.resolve("gemma4:cloud"))
    payload = config.to_dict()
    assert payload["model_profile"]["tier"] == "standard"
    assert payload["model_profile"]["context_length"] == 128000
    assert payload["context"]["max_context_tokens"] == 128000
