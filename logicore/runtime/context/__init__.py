"""
Context Window Management: Unified context management module.

This module contains ALL context management logic:
- ContextEngine: Public facade, main entry point for the agent's chat loop
- ContextWindowManager: Internal orchestrator (masking → compression → truncation)
- ContextManagementResult / EngineResult: Result types
- TokenEstimator: Token counting and model context window lookup
- TokenBudget: Model-specific token tracking
- CompressionService: Intelligent history summarization
- ToolOutputMaskingService: Backward-scanned FIFO masking
- PromptAssembler: System prompt construction
- MessagePipeline: System message injection/removal
- ToolOutputDistiller: Per-tool-call result truncation
"""

from logicore.runtime.context.manager import (
    ContextWindowManager,
    ContextManagementResult,
    ContextEngine,
    EngineResult,
)
from logicore.runtime.context.token_budget import TokenBudget, TokenUsage, TokenCategory
from logicore.runtime.context.compression import CompressionService, CompressionResult
from logicore.runtime.context.masking import ToolOutputMaskingService, MaskingResult
from logicore.runtime.context.token_estimator import (
    TokenEstimator,
    MODEL_CONTEXT_WINDOWS,
    get_model_context_window,
    get_tiktoken_counter,
    estimate_tokens,
    estimate_message_tokens,
)
from logicore.runtime.context.prompt_assembler import PromptAssembler
from logicore.runtime.context.message_pipeline import MessagePipeline
from logicore.runtime.context.tool_output_distiller import ToolOutputDistiller

__all__ = [
    # Facade
    "ContextEngine",
    "EngineResult",
    # Pipeline
    "ContextWindowManager",
    "ContextManagementResult",
    # Token
    "TokenEstimator",
    "TokenBudget",
    "TokenCategory",
    "TokenUsage",
    # Compression
    "CompressionService",
    "CompressionResult",
    # Masking
    "ToolOutputMaskingService",
    "MaskingResult",
    # Utilities
    "PromptAssembler",
    "MessagePipeline",
    "ToolOutputDistiller",
    # Token estimator utilities
    "MODEL_CONTEXT_WINDOWS",
    "get_model_context_window",
    "get_tiktoken_counter",
    "estimate_tokens",
    "estimate_message_tokens",
]
