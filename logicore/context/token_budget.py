"""
Token Budget Management

Model-aware context window tracking with per-category token usage breakdown.

Usage:
    from logicore.context.token_budget import TokenBudget, get_model_context_window
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any

# === Model context windows ===
# Authoritative mapping of model names to context window sizes

MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # OpenAI
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 128000,
    "gpt-3.5-turbo": 16385,
    # Anthropic
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-4-opus": 200000,
    # Google
    "gemini-pro": 32000,
    "gemini-1.5-pro": 1000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.5-pro": 1000000,
    # Ollama / Local
    "llama3": 8192,
    "llama3.1": 128000,
    "llama3.2": 128000,
    "mistral": 8192,
    "mixtral": 32768,
    "qwen": 32768,
    "gpt-oss": 128000,
    "gpt-oss:20b-cloud": 128000,
    # Default
    "default": 4096,
}


def get_model_context_window(model_name: str) -> int:
    """
    Get context window size for a model.
    
    Tries:
    1. Exact match
    2. Prefix match (e.g., "gpt-4-turbo-preview" -> "gpt-4-turbo")
    3. Contains match (e.g., "my-llama3-model" -> "llama3")
    4. Default fallback
    """
    # Exact match
    if model_name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_name]
    
    # Prefix match
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if model_name.startswith(known):
            return window
    
    # Contains match
    model_lower = model_name.lower()
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if known.lower() in model_lower:
            return window
    
    return MODEL_CONTEXT_WINDOWS["default"]


class TokenCategory(Enum):
    """Categories of token usage."""
    SYSTEM = "system"
    TOOLS = "tools"
    MESSAGES = "messages"
    TOOL_RESULTS = "tool_results"
    FILES = "files"
    OTHER = "other"


@dataclass
class TokenUsage:
    """Token usage breakdown by category."""
    system: int = 0
    tools: int = 0
    messages: int = 0
    tool_results: int = 0
    files: int = 0
    other: int = 0
    
    @property
    def total(self) -> int:
        return (
            self.system + self.tools + self.messages +
            self.tool_results + self.files + self.other
        )
    
    def to_dict(self) -> Dict[str, int]:
        return {
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "tool_results": self.tool_results,
            "files": self.files,
            "other": self.other,
            "total": self.total,
        }
    
    def percentages(self, context_window: int) -> Dict[str, float]:
        """Get usage as percentages of context window."""
        if context_window == 0:
            return {}
        
        return {
            "system": (self.system / context_window) * 100,
            "tools": (self.tools / context_window) * 100,
            "messages": (self.messages / context_window) * 100,
            "tool_results": (self.tool_results / context_window) * 100,
            "files": (self.files / context_window) * 100,
            "other": (self.other / context_window) * 100,
            "total": (self.total / context_window) * 100,
        }


@dataclass
class TokenBudget:
    """
    Tracks token budget for a session.
    
    Usage:
        budget = TokenBudget(model_name="gpt-4o")
        
        # Record usage
        budget.add_usage(TokenCategory.MESSAGES, 500)
        budget.add_usage(TokenCategory.TOOL_RESULTS, 1200)
        
        # Check status
        if budget.should_compress():
            # Trigger compression
            ...
        
        print(budget.remaining)
    """
    
    model_name: str = "default"
    compression_threshold: float = 0.85
    warning_threshold: float = 0.75
    
    # Usage tracking
    usage: TokenUsage = field(default_factory=TokenUsage)
    
    # Context window (auto-populated)
    context_window: int = field(init=False)
    
    # History
    _usage_history: list = field(default_factory=list, repr=False)
    
    def __post_init__(self):
        self.context_window = get_model_context_window(self.model_name)
    
    @property
    def used(self) -> int:
        """Total tokens used."""
        return self.usage.total
    
    @property
    def remaining(self) -> int:
        """Tokens remaining in budget."""
        return max(0, self.context_window - self.used)
    
    @property
    def usage_ratio(self) -> float:
        """Usage as ratio (0.0 to 1.0)."""
        if self.context_window == 0:
            return 0.0
        return self.used / self.context_window
    
    @property
    def usage_percent(self) -> float:
        """Usage as percentage."""
        return self.usage_ratio * 100
    
    def add_usage(self, category: TokenCategory, tokens: int) -> None:
        """Add token usage to a category."""
        if category == TokenCategory.SYSTEM:
            self.usage.system += tokens
        elif category == TokenCategory.TOOLS:
            self.usage.tools += tokens
        elif category == TokenCategory.MESSAGES:
            self.usage.messages += tokens
        elif category == TokenCategory.TOOL_RESULTS:
            self.usage.tool_results += tokens
        elif category == TokenCategory.FILES:
            self.usage.files += tokens
        else:
            self.usage.other += tokens
        
        self._usage_history.append({
            "category": category.value,
            "tokens": tokens,
            "timestamp": datetime.now().isoformat(),
        })
    
    def set_usage(self, category: TokenCategory, tokens: int) -> None:
        """Set token usage for a category (replaces existing)."""
        if category == TokenCategory.SYSTEM:
            self.usage.system = tokens
        elif category == TokenCategory.TOOLS:
            self.usage.tools = tokens
        elif category == TokenCategory.MESSAGES:
            self.usage.messages = tokens
        elif category == TokenCategory.TOOL_RESULTS:
            self.usage.tool_results = tokens
        elif category == TokenCategory.FILES:
            self.usage.files = tokens
        else:
            self.usage.other = tokens
    
    def should_warn(self) -> bool:
        """Check if we should warn about usage."""
        return self.usage_ratio >= self.warning_threshold
    
    def should_compress(self) -> bool:
        """Check if compression should be triggered."""
        return self.usage_ratio >= self.compression_threshold
    
    def is_exceeded(self) -> bool:
        """Check if budget is exceeded."""
        return self.used >= self.context_window
    
    def reset(self) -> None:
        """Reset all usage."""
        self.usage = TokenUsage()
        self._usage_history.clear()
    
    def get_status(self) -> Dict[str, Any]:
        """Get budget status summary."""
        return {
            "model": self.model_name,
            "context_window": self.context_window,
            "used": self.used,
            "remaining": self.remaining,
            "usage_percent": round(self.usage_percent, 1),
            "should_compress": self.should_compress(),
            "should_warn": self.should_warn(),
            "is_exceeded": self.is_exceeded(),
            "breakdown": self.usage.to_dict(),
            "breakdown_percent": self.usage.percentages(self.context_window),
        }


def estimate_tokens(text: str) -> int:
    """
    Estimate token count from text.
    
    Uses the common heuristic: ~4 characters per token.
    For more accurate counting, use a proper tokenizer.
    """
    if not text:
        return 0
    return len(text) // 4


def estimate_message_tokens(messages: list) -> int:
    """
    Estimate tokens for a list of messages.
    
    Accounts for message overhead (role, structure).
    """
    total = 0
    for msg in messages:
        # Role overhead (~4 tokens)
        total += 4
        
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Multi-part content
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        # Images use significant tokens
                        total += 1000  # Rough estimate
        
        # Tool calls
        if "tool_calls" in msg:
            total += len(str(msg["tool_calls"])) // 10
    
    return total


# === Exports ===

__all__ = [
    # Core classes
    "TokenBudget",
    "TokenUsage",
    "TokenCategory",
    # Functions
    "get_model_context_window",
    "estimate_tokens",
    "estimate_message_tokens",
    # Constants
    "MODEL_CONTEXT_WINDOWS",
]
