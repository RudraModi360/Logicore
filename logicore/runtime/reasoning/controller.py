"""
ReasoningController: Dynamic reasoning level adjustment during execution.

Provides runtime control over reasoning depth, including:
- Manual level adjustment via API
- Automatic escalation based on task complexity
- De-escalation for simple queries
- Reasoning state tracking across turns
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from datetime import datetime

from logicore.runtime.reasoning.config import (
    ReasoningLevel,
    ReasoningConfig,
    get_reasoning_system_prompt_addon,
)


@dataclass
class ReasoningState:
    """Tracks reasoning state across turns."""
    
    current_level: ReasoningLevel
    original_level: ReasoningLevel
    escalation_count: int = 0
    de_escalation_count: int = 0
    last_adjustment: Optional[datetime] = None
    adjustment_history: List[dict] = field(default_factory=list)


class ReasoningController:
    """
    Controls reasoning level dynamically during agent execution.
    
    Features:
    - Set reasoning level programmatically
    - Auto-escalate on complex queries
    - Track reasoning adjustments
    - Generate appropriate system prompt addons
    
    Usage:
        controller = ReasoningController(ReasoningConfig(level=ReasoningLevel.MEDIUM))
        
        # Manual adjustment
        controller.set_level(ReasoningLevel.HIGH)
        
        # Auto-adjust based on query
        adjusted = controller.adjust_for_query("Debug this complex issue")
        
        # Get system prompt addon
        prompt = controller.get_system_prompt_addon()
    """
    
    def __init__(self, config: Optional[ReasoningConfig] = None):
        """Initialize controller with optional config."""
        self.config = config or ReasoningConfig()
        self.state = ReasoningState(
            current_level=self.config.level,
            original_level=self.config.level,
        )
        self._level_change_callbacks: List[Callable[[ReasoningLevel, ReasoningLevel], None]] = []
    
    @property
    def current_level(self) -> ReasoningLevel:
        """Get current reasoning level."""
        return self.state.current_level
    
    @property
    def thinking_budget(self) -> int:
        """Get current thinking budget based on level."""
        return self.config.get_thinking_budget_for_level()
    
    def set_level(self, level: ReasoningLevel, reason: str = "manual") -> None:
        """
        Set reasoning level explicitly.
        
        Args:
            level: New reasoning level
            reason: Reason for change (for tracking)
        """
        old_level = self.state.current_level
        if old_level == level:
            return
        
        self.state.current_level = level
        self.state.last_adjustment = datetime.now()
        self.state.adjustment_history.append({
            "from": old_level.name,
            "to": level.name,
            "reason": reason,
            "timestamp": self.state.last_adjustment.isoformat(),
        })
        
        # Notify callbacks
        for callback in self._level_change_callbacks:
            try:
                callback(old_level, level)
            except Exception:
                pass  # Don't fail on callback errors
    
    def escalate(self, reason: str = "complexity_detected") -> ReasoningLevel:
        """
        Escalate reasoning level by one step.
        
        Returns:
            New reasoning level
        """
        level_order = [
            ReasoningLevel.MINIMAL,
            ReasoningLevel.LOW,
            ReasoningLevel.MEDIUM,
            ReasoningLevel.HIGH,
            ReasoningLevel.DEEP,
        ]
        current_idx = level_order.index(self.state.current_level)
        if current_idx < len(level_order) - 1:
            new_level = level_order[current_idx + 1]
            self.set_level(new_level, reason)
            self.state.escalation_count += 1
        return self.state.current_level
    
    def de_escalate(self, reason: str = "simple_query") -> ReasoningLevel:
        """
        De-escalate reasoning level by one step.
        
        Returns:
            New reasoning level
        """
        level_order = [
            ReasoningLevel.MINIMAL,
            ReasoningLevel.LOW,
            ReasoningLevel.MEDIUM,
            ReasoningLevel.HIGH,
            ReasoningLevel.DEEP,
        ]
        current_idx = level_order.index(self.state.current_level)
        if current_idx > 0:
            new_level = level_order[current_idx - 1]
            self.set_level(new_level, reason)
            self.state.de_escalation_count += 1
        return self.state.current_level
    
    def reset(self) -> None:
        """Reset to original reasoning level."""
        self.set_level(self.state.original_level, "reset")
        self.state.escalation_count = 0
        self.state.de_escalation_count = 0
    
    def adjust_for_query(self, query: str) -> ReasoningLevel:
        """
        Automatically adjust reasoning level based on query complexity.
        
        Args:
            query: User query to analyze
            
        Returns:
            Adjusted reasoning level
        """
        if not self.config.auto_escalate:
            return self.state.current_level
        
        # Check for escalation triggers
        if self._should_escalate(query):
            return self.escalate("auto_escalation_query_complexity")
        
        # Check for de-escalation triggers
        if self._should_de_escalate(query):
            return self.de_escalate("auto_de_escalation_simple_query")
        
        return self.state.current_level
    
    def _should_escalate(self, query: str) -> bool:
        """Check if query should trigger escalation."""
        query_lower = query.lower()
        
        # Check configured keywords
        if any(kw in query_lower for kw in self.config.auto_escalate_keywords):
            return True
        
        # Check for question complexity patterns
        complex_patterns = [
            r"why does.+not work",
            r"how (can|do|should) (i|we).+multiple",
            r"what('s| is) the (best|optimal|right) (way|approach)",
            r"debug.+(error|issue|problem|bug)",
            r"(analyze|investigate|diagnose)",
            r"step.?by.?step",
            r"(comprehensive|thorough|detailed) (analysis|review|audit)",
        ]
        for pattern in complex_patterns:
            if re.search(pattern, query_lower):
                return True
        
        return False
    
    def _should_de_escalate(self, query: str) -> bool:
        """Check if query should trigger de-escalation."""
        query_lower = query.lower()
        
        # Simple query patterns
        simple_patterns = [
            r"^(what|when|where|who) is \w+\??$",  # Simple factual questions
            r"^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure)$",  # Greetings/acknowledgments
            r"^(show|list|print|display) \w+$",  # Simple show commands
            r"^(open|run|execute) \w+$",  # Simple action commands
        ]
        for pattern in simple_patterns:
            if re.match(pattern, query_lower.strip()):
                return True
        
        # Short queries (< 30 chars) are usually simple
        if len(query.strip()) < 30 and "?" not in query:
            return True
        
        return False
    
    def get_system_prompt_addon(self) -> str:
        """
        Get system prompt addon for current reasoning level.
        
        Returns:
            System prompt text to inject
        """
        # Create a temporary config with current level
        current_config = ReasoningConfig(
            level=self.state.current_level,
            thinking_budget=self.config.thinking_budget,
            include_thoughts=self.config.include_thoughts,
            show_reasoning_steps=self.config.show_reasoning_steps,
            approval_mode=self.config.approval_mode,
        )
        return get_reasoning_system_prompt_addon(current_config)
    
    def on_level_change(self, callback: Callable[[ReasoningLevel, ReasoningLevel], None]) -> None:
        """
        Register callback for level changes.
        
        Args:
            callback: Function called with (old_level, new_level)
        """
        self._level_change_callbacks.append(callback)
    
    def get_state_summary(self) -> dict:
        """Get summary of reasoning state for telemetry/debugging."""
        return {
            "current_level": self.state.current_level.name,
            "original_level": self.state.original_level.name,
            "escalation_count": self.state.escalation_count,
            "de_escalation_count": self.state.de_escalation_count,
            "thinking_budget": self.thinking_budget,
            "adjustment_count": len(self.state.adjustment_history),
        }
