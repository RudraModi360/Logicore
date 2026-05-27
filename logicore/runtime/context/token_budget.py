"""
TokenBudget: Model-specific token tracking and budget management.

Features:
- Track token usage across different categories
- Model-specific context window awareness
- Budget forecasting and alerts
- Support for multiple token counting strategies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable

from logicore.runtime.config import RuntimeConfig


class TokenCategory(Enum):
    """Categories for token accounting."""
    SYSTEM = "system"          # System prompt
    TOOLS = "tools"            # Tool definitions
    MESSAGES = "messages"      # User/assistant messages
    TOOL_RESULTS = "tool_results"  # Tool execution results
    CONTEXT = "context"        # Injected context (memory, files)
    RESERVED = "reserved"      # Reserved for response


@dataclass
class TokenUsage:
    """Token usage snapshot."""
    total: int = 0
    by_category: Dict[TokenCategory, int] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        # Initialize all categories to 0
        for cat in TokenCategory:
            if cat not in self.by_category:
                self.by_category[cat] = 0
    
    @property
    def user_tokens(self) -> int:
        """Tokens used by user content (messages + context)."""
        return (
            self.by_category.get(TokenCategory.MESSAGES, 0) +
            self.by_category.get(TokenCategory.CONTEXT, 0)
        )
    
    @property
    def system_tokens(self) -> int:
        """Tokens used by system content (prompt + tools)."""
        return (
            self.by_category.get(TokenCategory.SYSTEM, 0) +
            self.by_category.get(TokenCategory.TOOLS, 0)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "total": self.total,
            "by_category": {k.value: v for k, v in self.by_category.items()},
            "timestamp": self.timestamp.isoformat(),
        }


class TokenBudget:
    """
    Tracks token usage and manages budget for a model.
    
    Features:
    - Model-specific context window awareness
    - Category-based token tracking
    - Budget forecasting
    - Threshold alerts
    
    Usage:
        budget = TokenBudget(config, model_name="gpt-4")
        
        # Track usage
        budget.add_tokens(TokenCategory.MESSAGES, 500)
        budget.add_tokens(TokenCategory.TOOL_RESULTS, 1000)
        
        # Check budget
        if budget.should_compress():
            # Trigger compression
            pass
        
        # Get remaining
        remaining = budget.get_remaining_tokens()
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
    ):
        """
        Args:
            config: Runtime configuration
            model_name: Name of the model for context window lookup
            token_counter: Optional custom token counting function
        """
        self.config = config
        self.model_name = model_name
        self._token_counter = token_counter or self._default_token_counter
        
        # Get model-specific context window
        self.context_window = config.get_model_context_window(model_name)
        
        # Calculate thresholds
        self.compression_threshold = config.get_compression_threshold_for_model(model_name)
        
        # Reserved tokens for response (ensure model can respond)
        self.reserved_for_response = min(4096, self.context_window // 4)
        
        # Current usage
        self._usage = TokenUsage()
        
        # History for tracking trends
        self._history: List[TokenUsage] = []
        self._max_history = 100
    
    @staticmethod
    def _default_token_counter(text: str) -> int:
        """
        Default token estimation: ~4 characters per token.
        
        This is a rough heuristic. For accuracy, use model-specific tokenizers.
        """
        if not text:
            return 0
        return len(text) // 4
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text using configured counter."""
        return self._token_counter(text)
    
    def add_tokens(self, category: TokenCategory, count: int) -> None:
        """Add tokens to a category."""
        self._usage.by_category[category] = (
            self._usage.by_category.get(category, 0) + count
        )
        self._usage.total = sum(self._usage.by_category.values())
        self._usage.timestamp = datetime.now()
    
    def set_tokens(self, category: TokenCategory, count: int) -> None:
        """Set tokens for a category (replaces existing)."""
        old_count = self._usage.by_category.get(category, 0)
        self._usage.by_category[category] = count
        self._usage.total = self._usage.total - old_count + count
        self._usage.timestamp = datetime.now()
    
    def get_usage(self) -> TokenUsage:
        """Get current token usage."""
        return self._usage
    
    def get_remaining_tokens(self) -> int:
        """Get tokens remaining in budget."""
        return max(0, self.context_window - self._usage.total - self.reserved_for_response)
    
    def get_usage_ratio(self) -> float:
        """Get usage as ratio of context window (0.0-1.0)."""
        if self.context_window == 0:
            return 1.0
        return self._usage.total / self.context_window
    
    def should_compress(self) -> bool:
        """Check if compression should be triggered."""
        return self._usage.total >= self.compression_threshold
    
    def should_mask_tool_outputs(self) -> bool:
        """Check if tool output masking should be triggered."""
        tool_result_tokens = self._usage.by_category.get(TokenCategory.TOOL_RESULTS, 0)
        return tool_result_tokens >= self.config.context.protection_threshold_tokens
    
    def get_compression_target(self) -> int:
        """Get target token count after compression."""
        # Target: preserve_recent_ratio of the compression threshold
        target = int(self.compression_threshold * self.config.context.preserve_recent_ratio)
        return max(target, 1000)  # Minimum 1000 tokens
    
    def record_snapshot(self) -> None:
        """Record current usage in history."""
        self._history.append(TokenUsage(
            total=self._usage.total,
            by_category=dict(self._usage.by_category),
            timestamp=datetime.now(),
        ))
        
        # Trim history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
    
    def get_growth_rate(self) -> float:
        """Get average token growth rate per turn."""
        if len(self._history) < 2:
            return 0.0
        
        # Calculate average growth
        growths = []
        for i in range(1, len(self._history)):
            growth = self._history[i].total - self._history[i - 1].total
            growths.append(growth)
        
        return sum(growths) / len(growths) if growths else 0.0
    
    def estimate_turns_until_compression(self) -> Optional[int]:
        """Estimate how many turns until compression is needed."""
        remaining = self.compression_threshold - self._usage.total
        growth_rate = self.get_growth_rate()
        
        if growth_rate <= 0:
            return None  # No growth, won't hit threshold
        
        return int(remaining / growth_rate)
    
    def reset(self) -> None:
        """Reset usage tracking."""
        self._usage = TokenUsage()
        self._history.clear()
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize budget state."""
        return {
            "model_name": self.model_name,
            "context_window": self.context_window,
            "compression_threshold": self.compression_threshold,
            "reserved_for_response": self.reserved_for_response,
            "current_usage": self._usage.to_dict(),
            "remaining_tokens": self.get_remaining_tokens(),
            "usage_ratio": self.get_usage_ratio(),
            "should_compress": self.should_compress(),
            "growth_rate": self.get_growth_rate(),
            "turns_until_compression": self.estimate_turns_until_compression(),
        }
