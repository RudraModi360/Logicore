"""
Context Engine — Unified context management for the agent.

Replaces the legacy ContextMiddleware with a proper multi-stage pipeline:
  Stage 0: Token estimation
  Stage 1: Tool output masking (fast, no LLM)
  Stage 2: Compression via LLM summarization
  Stage 3: Emergency truncation (last resort)

Also provides:
  - Prompt assembly (system prompt truncation)
  - Message pipeline (injection/removal of system hints)
  - Tool output distillation (per-result truncation)

Public API:
  - ContextEngine: Main entry point for context management
  - ContextManagementResult: Result of context operations (also exported as EngineResult)
  - TokenEstimator: Token counting utilities
  - PromptAssembler: System prompt construction
  - MessagePipeline: Message injection/removal
  - ToolOutputDistiller: Per-result truncation
"""

from .engine import ContextEngine, EngineResult
from .token_estimator import TokenEstimator
from .prompt_assembler import PromptAssembler
from .message_pipeline import MessagePipeline
from .tool_output_distiller import ToolOutputDistiller

__all__ = [
    "ContextEngine",
    "EngineResult",
    "TokenEstimator",
    "PromptAssembler",
    "MessagePipeline",
    "ToolOutputDistiller",
]
