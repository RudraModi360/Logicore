"""
Context Window Management: Intelligent context compression and masking.

Components:
- ContextWindowManager: Orchestrates context budget and compression
- ContextManagementResult: Result of context management operations
- TokenBudget: Model-specific token tracking
- CompressionService: Intelligent history summarization (async, outside main loop)
- ToolOutputMaskingService: Backward-scanned FIFO masking for tool outputs
- DistillationService: Large output summarization via secondary LLM
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
