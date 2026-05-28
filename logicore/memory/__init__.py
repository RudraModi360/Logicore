"""
Agentry Memory Module

The old memory system (VFS, SQLite storage, middleware) has been replaced
by SimpleMem integration for better context engineering.

For memory features, use:
- logicore.simplemem.AgentrySimpleMem - Main memory integration
- backend.services.storage - Unified storage interface

For context management:
- logicore.memory.context_middleware - Context compression
- logicore.memory.token_budget - Token budget tracking

Legacy project_memory.py is retained for SmartAgent project mode.
"""

# Keep project_memory for SmartAgent compatibility
from .project_memory import ProjectMemory
from .context_middleware import ContextMiddleware
from .token_budget import (
    TokenBudget,
    TokenUsage,
    TokenCategory,
    get_model_context_window,
    estimate_tokens,
    estimate_message_tokens,
    MODEL_CONTEXT_WINDOWS,
)

__all__ = [
    # Legacy
    "ProjectMemory",
    # Context
    "ContextMiddleware",
    # Token Budget
    "TokenBudget",
    "TokenUsage",
    "TokenCategory",
    "get_model_context_window",
    "estimate_tokens",
    "estimate_message_tokens",
    "MODEL_CONTEXT_WINDOWS",
]
