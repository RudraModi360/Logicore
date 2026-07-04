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
"""

from .engine import ContextEngine
from .token_estimator import TokenEstimator
from .prompt_assembler import PromptAssembler
from .message_pipeline import MessagePipeline
from .tool_output_distiller import ToolOutputDistiller

__all__ = [
    "ContextEngine",
    "TokenEstimator",
    "PromptAssembler",
    "MessagePipeline",
    "ToolOutputDistiller",
]
