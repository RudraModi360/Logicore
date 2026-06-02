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
    
    # -------------------------------------------------------------------------
    # Response-Based Adjustment (ThoughtParser Integration)
    # -------------------------------------------------------------------------
    
    def adjust_for_response(
        self, 
        response_text: str,
        complexity_threshold: float = 0.5,
    ) -> ReasoningLevel:
        """
        Adjust reasoning level based on response complexity analysis.
        
        Uses ThoughtParser to analyze structured thinking in the response.
        High complexity responses may trigger escalation for subsequent turns.
        
        Args:
            response_text: Model response to analyze
            complexity_threshold: Threshold for escalation (0-1)
        
        Returns:
            Adjusted reasoning level
        """
        if not self.config.auto_escalate:
            return self.state.current_level
        
        from logicore.runtime.reasoning.thought_parser import ThoughtParser
        
        parser = ThoughtParser()
        analysis = parser.parse(response_text)
        
        # If response shows high complexity, escalate for next turn
        if analysis.complexity_score >= complexity_threshold:
            # Only escalate if not already at max
            if self.state.current_level != ReasoningLevel.DEEP:
                return self.escalate(
                    f"response_complexity_{analysis.complexity_score:.2f}"
                )
        
        # If response is very simple and we're escalated, consider de-escalation
        elif analysis.complexity_score < 0.2 and not analysis.has_structured_thinking:
            if self.state.current_level.value > self.state.original_level.value:
                return self.de_escalate("response_simple")
        
        return self.state.current_level
    
    def analyze_response(self, response_text: str) -> dict:
        """
        Analyze a response for reasoning patterns.
        
        Returns analysis data useful for debugging and telemetry.
        
        Args:
            response_text: Model response to analyze
        
        Returns:
            Dict with analysis results
        """
        from logicore.runtime.reasoning.thought_parser import ThoughtParser
        
        parser = ThoughtParser()
        analysis = parser.parse(response_text)
        
        return {
            "has_structured_thinking": analysis.has_structured_thinking,
            "thought_count": analysis.thought_count,
            "complexity_score": analysis.complexity_score,
            "thought_types": analysis.metadata.get("thought_types", []),
            "subjects": analysis.subjects,
        }
    
    # -------------------------------------------------------------------------
    # Hook Integration
    # -------------------------------------------------------------------------
    
    def create_after_model_hook(self):
        """
        Create an AFTER_MODEL hook for automatic reasoning adjustment.
        
        This hook analyzes model responses and adjusts reasoning level
        for subsequent turns based on detected complexity.
        
        Usage:
            from logicore.runtime.hooks import HookSystem, HookPoint
            
            hooks = HookSystem()
            controller = ReasoningController()
            
            hooks.add_hook(
                name="reasoning_auto_adjust",
                hook_point=HookPoint.AFTER_MODEL,
                hook_fn=controller.create_after_model_hook(),
                priority=50,
            )
        """
        from logicore.runtime.hooks import HookContext, HookResult, HookAction
        
        async def after_model_hook(ctx: HookContext) -> HookResult:
            """Hook that adjusts reasoning based on response complexity."""
            if ctx.model_response and ctx.model_response.content:
                old_level = self.state.current_level
                new_level = self.adjust_for_response(ctx.model_response.content)
                
                return HookResult(
                    action=HookAction.CONTINUE,
                    metadata={
                        "reasoning_adjusted": old_level != new_level,
                        "old_level": old_level.name,
                        "new_level": new_level.name,
                    }
                )
            
            return HookResult()
        
        return after_model_hook
    
    def create_before_model_hook(self):
        """
        Create a BEFORE_MODEL hook for injecting reasoning instructions.
        
        This hook adds reasoning-specific system prompt addons based
        on the current reasoning level.
        
        Usage:
            hooks.add_hook(
                name="reasoning_prompt_injection",
                hook_point=HookPoint.BEFORE_MODEL,
                hook_fn=controller.create_before_model_hook(),
                priority=10,  # Early to influence other hooks
            )
        """
        from logicore.runtime.hooks import HookContext, HookResult, HookAction
        
        async def before_model_hook(ctx: HookContext) -> HookResult:
            """Hook that injects reasoning instructions."""
            addon = self.get_system_prompt_addon()
            
            if not addon:
                return HookResult()
            
            # Find system message and append reasoning addon
            messages = list(ctx.messages)
            system_idx = None
            
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    system_idx = i
                    break
            
            if system_idx is not None:
                # Append to existing system message
                current_content = messages[system_idx].get("content", "")
                messages[system_idx] = {
                    **messages[system_idx],
                    "content": f"{current_content}\n\n{addon}"
                }
            else:
                # Insert new system message at start
                messages.insert(0, {"role": "system", "content": addon})
            
            return HookResult(
                action=HookAction.MODIFY,
                modified_messages=messages,
                metadata={
                    "reasoning_level": self.state.current_level.name,
                    "thinking_budget": self.thinking_budget,
                }
            )
        
        return before_model_hook
    
    def register_hooks(self, hook_system) -> None:
        """
        Register all reasoning hooks with a HookSystem.
        
        Args:
            hook_system: HookSystem instance to register with
        
        Usage:
            from logicore.runtime.hooks import HookSystem
            from logicore.runtime.reasoning import ReasoningController
            
            hooks = HookSystem()
            controller = ReasoningController(config)
            controller.register_hooks(hooks)
        """
        from logicore.runtime.hooks import HookPoint
        
        hook_system.add_hook(
            name="reasoning_prompt_injection",
            hook_point=HookPoint.BEFORE_MODEL,
            hook_fn=self.create_before_model_hook(),
            priority=10,
            description="Injects reasoning-level system prompt addons",
        )
        
        hook_system.add_hook(
            name="reasoning_auto_adjust",
            hook_point=HookPoint.AFTER_MODEL,
            hook_fn=self.create_after_model_hook(),
            priority=50,
            description="Auto-adjusts reasoning level based on response complexity",
        )
