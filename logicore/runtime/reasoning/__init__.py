"""
Reasoning Configuration Module

Provides configurable reasoning levels and thinking budgets for agents,
inspired by gemini-cli's thinkingConfig patterns.

Components:
- ReasoningLevel: Enum defining reasoning depth (MINIMAL → DEEP)
- ReasoningConfig: Configuration dataclass for reasoning behavior
- ReasoningController: Dynamic reasoning level adjustment during execution

Usage:
    from logicore.runtime.reasoning import ReasoningLevel, ReasoningConfig
    
    config = ReasoningConfig(
        level=ReasoningLevel.HIGH,
        thinking_budget=4096,
        include_thoughts=True
    )
"""

from logicore.runtime.reasoning.config import (
    ReasoningLevel,
    ReasoningConfig,
    REASONING_PRESETS,
    get_reasoning_system_prompt_addon,
)
from logicore.runtime.reasoning.controller import ReasoningController

__all__ = [
    "ReasoningLevel",
    "ReasoningConfig",
    "ReasoningController",
    "REASONING_PRESETS",
    "get_reasoning_system_prompt_addon",
]
