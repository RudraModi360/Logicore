"""
Reasoning Configuration Module

Provides configurable reasoning levels and thinking budgets for agents,
inspired by gemini-cli's thinkingConfig patterns.

Components:
- ReasoningLevel: Enum defining reasoning depth (MINIMAL → DEEP)
- ReasoningConfig: Configuration dataclass for reasoning behavior
- ReasoningController: Dynamic reasoning level adjustment during execution
- ThoughtParser: Structured thought extraction from model responses

Usage:
    from logicore.runtime.reasoning import ReasoningLevel, ReasoningConfig
    
    config = ReasoningConfig(
        level=ReasoningLevel.HIGH,
        thinking_budget=4096,
        include_thoughts=True
    )
    
    # Parse thoughts from response
    from logicore.runtime.reasoning import ThoughtParser
    parser = ThoughtParser()
    analysis = parser.parse(model_response)
"""

from logicore.runtime.reasoning.config import (
    ReasoningLevel,
    ReasoningConfig,
    REASONING_PRESETS,
    get_reasoning_system_prompt_addon,
)
from logicore.runtime.reasoning.controller import ReasoningController
from logicore.runtime.reasoning.thought_parser import (
    ThoughtParser,
    ThoughtAnalysis,
    ParsedThought,
    ThoughtType,
    parse_thoughts,
    extract_subject_descriptions,
)

__all__ = [
    # Config
    "ReasoningLevel",
    "ReasoningConfig",
    "ReasoningController",
    "REASONING_PRESETS",
    "get_reasoning_system_prompt_addon",
    # Thought Parsing
    "ThoughtParser",
    "ThoughtAnalysis",
    "ParsedThought",
    "ThoughtType",
    "parse_thoughts",
    "extract_subject_descriptions",
]
