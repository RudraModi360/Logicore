"""
Context Window Management: Internal implementation details.

This module contains the internal implementation of context management:
- ContextWindowManager: Orchestrates context budget and compression
- ContextManagementResult: Result of context management operations
- TokenBudget: Model-specific token tracking
- CompressionService: Intelligent history summarization (async, outside main loop)
- ToolOutputMaskingService: Backward-scanned FIFO masking for tool outputs

For the public API, use logicore.context_engine instead.
"""

from logicore.runtime.context.manager import ContextWindowManager, ContextManagementResult
from logicore.runtime.context.token_budget import TokenBudget, TokenUsage, TokenCategory
from logicore.runtime.context.compression import CompressionService, CompressionResult
from logicore.runtime.context.masking import ToolOutputMaskingService, MaskingResult

__all__ = [
    "ContextWindowManager",
    "ContextManagementResult",
    "TokenBudget",
    "TokenUsage",
    "TokenCategory",
    "CompressionService",
    "CompressionResult",
    "ToolOutputMaskingService",
    "MaskingResult",
]
