"""
Recovery Strategies: Composable strategies for loop recovery.

Strategies:
- RethinkStrategy: Inject guidance to try different approach
- ToolCooldownStrategy: Temporarily disable repeated tool
- ContextResetStrategy: Clear short-term reasoning
- SummarizeProgressStrategy: Summarize what's been done
- ProviderFallbackStrategy: Switch to different model

Strategies are composable and applied based on escalation level.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any

from logicore.runtime.config import RecoveryEscalationLevel


class RecoveryActionType(Enum):
    """Types of recovery actions."""
    INJECT_GUIDANCE = "inject_guidance"
    COOL_DOWN_TOOL = "cool_down_tool"
    RESET_CONTEXT = "reset_context"
    SUMMARIZE = "summarize"
    SWITCH_MODEL = "switch_model"
    TERMINATE = "terminate"
    NO_OP = "no_op"


@dataclass
class RecoveryAction:
    """Action to take for loop recovery."""
    action_type: RecoveryActionType
    
    # For guidance injection
    guidance_message: Optional[str] = None
    
    # For tool cooldown
    tool_name: Optional[str] = None
    cooldown_seconds: int = 30
    
    # For context reset
    messages_to_keep: int = 10
    
    # For model switch
    fallback_model: Optional[str] = None
    
    # Metadata
    reason: Optional[str] = None
    escalation_level: Optional[RecoveryEscalationLevel] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "action_type": self.action_type.value,
            "guidance_message": self.guidance_message,
            "tool_name": self.tool_name,
            "cooldown_seconds": self.cooldown_seconds,
            "messages_to_keep": self.messages_to_keep,
            "fallback_model": self.fallback_model,
            "reason": self.reason,
            "escalation_level": self.escalation_level.value if self.escalation_level else None,
        }


class RecoveryStrategy(ABC):
    """Base class for recovery strategies."""
    
    @abstractmethod
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """
        Generate a recovery action for the detected loop.
        
        Args:
            loop_type: Type of loop detected
            detail: Detail about the loop
            escalation_level: Current escalation level
            session_context: Context about the session (tools used, etc.)
        
        Returns:
            RecoveryAction to apply
        """
        pass


class RethinkStrategy(RecoveryStrategy):
    """
    Inject guidance to encourage a different approach.
    
    This is the gentlest recovery strategy - it adds a system message
    asking the model to reconsider its approach without taking
    any forceful action.
    """
    
    GUIDANCE_TEMPLATES = {
        "consecutive_tool_calls": (
            "I notice you've been calling the same tool repeatedly with similar arguments. "
            "This might indicate the approach isn't working. Please:\n"
            "1. Analyze why the previous attempts didn't achieve the goal\n"
            "2. Consider a completely different approach\n"
            "3. If the tool is genuinely needed, try with different parameters\n"
            "4. If stuck, explain what you're trying to accomplish and ask for guidance"
        ),
        "content_repetition": (
            "I notice you're generating repetitive content. This often happens when:\n"
            "- You're uncertain how to proceed\n"
            "- The task might be ambiguous\n"
            "- You're trying to emphasize a point\n\n"
            "Please take a step back and:\n"
            "1. Clearly state what you understand the goal to be\n"
            "2. Identify what's blocking progress\n"
            "3. Propose a concrete next action or ask a clarifying question"
        ),
        "stagnant_state": (
            "It appears we haven't made progress recently. Let's reset our approach:\n"
            "1. What is the core objective we're trying to achieve?\n"
            "2. What have we tried so far and why didn't it work?\n"
            "3. What's a fundamentally different approach we could try?\n\n"
            "Sometimes the best path forward is to acknowledge we're stuck and ask for help."
        ),
        "semantic_loop": (
            "The conversation appears to be going in circles. To break this pattern:\n"
            "1. Stop and summarize the current situation in one sentence\n"
            "2. Identify the single most important next action\n"
            "3. If multiple approaches have failed, it may be time to ask the user for guidance\n\n"
            "Focus on taking ONE concrete action rather than planning multiple steps."
        ),
        "default": (
            "I notice we might be in a loop. Please:\n"
            "1. Pause and assess the current situation\n"
            "2. Consider if a different approach might work better\n"
            "3. If stuck, it's okay to ask for clarification or help"
        ),
    }
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate guidance injection action."""
        template = self.GUIDANCE_TEMPLATES.get(
            loop_type,
            self.GUIDANCE_TEMPLATES["default"],
        )
        
        # Customize based on context
        message = template
        if detail:
            message = f"{template}\n\nContext: {detail}"
        
        return RecoveryAction(
            action_type=RecoveryActionType.INJECT_GUIDANCE,
            guidance_message=message,
            reason=f"Loop detected: {loop_type}",
            escalation_level=escalation_level,
        )


class ToolCooldownStrategy(RecoveryStrategy):
    """
    Temporarily disable the tool that's being repeatedly called.
    
    This is a more forceful strategy that prevents the model from
    using a specific tool for a period of time.
    """
    
    def __init__(self, default_cooldown_seconds: int = 30):
        self.default_cooldown = default_cooldown_seconds
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate tool cooldown action."""
        # Extract tool name from detail or context
        tool_name = None
        
        if detail and "'" in detail:
            # Try to extract tool name from detail like "Tool 'xyz' called..."
            parts = detail.split("'")
            if len(parts) >= 2:
                tool_name = parts[1]
        
        if not tool_name:
            tool_name = session_context.get("last_tool_name")
        
        if not tool_name:
            # Fall back to guidance if no tool identified
            return RethinkStrategy().get_action(
                loop_type, detail, escalation_level, session_context
            )
        
        # Scale cooldown with escalation
        cooldown = self.default_cooldown
        if escalation_level == RecoveryEscalationLevel.TOOL_COOLDOWN:
            cooldown = self.default_cooldown * 2
        
        guidance = (
            f"The tool '{tool_name}' has been temporarily disabled because it was "
            f"being called repeatedly without progress. Please try a different approach "
            f"or use alternative tools. The tool will be available again in {cooldown} seconds."
        )
        
        return RecoveryAction(
            action_type=RecoveryActionType.COOL_DOWN_TOOL,
            tool_name=tool_name,
            cooldown_seconds=cooldown,
            guidance_message=guidance,
            reason=f"Tool '{tool_name}' causing loop",
            escalation_level=escalation_level,
        )


class ContextResetStrategy(RecoveryStrategy):
    """
    Clear short-term reasoning while preserving core context.
    
    Removes recent messages that may be polluting the context
    while keeping the system prompt and initial conversation.
    """
    
    def __init__(self, messages_to_keep: int = 10):
        self.messages_to_keep = messages_to_keep
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate context reset action."""
        guidance = (
            "The conversation context has been partially reset to break a detected loop. "
            "Recent messages have been summarized. Please:\n"
            "1. Review the current state based on available context\n"
            "2. Identify the core objective\n"
            "3. Proceed with a fresh approach"
        )
        
        return RecoveryAction(
            action_type=RecoveryActionType.RESET_CONTEXT,
            messages_to_keep=self.messages_to_keep,
            guidance_message=guidance,
            reason=f"Context reset due to {loop_type}",
            escalation_level=escalation_level,
        )


class SummarizeProgressStrategy(RecoveryStrategy):
    """
    Summarize what's been accomplished so far.
    
    Creates a summary of progress to help the model understand
    the current state without repetitive context.
    """
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate summarize action."""
        # Build summary from session context
        tools_used = session_context.get("tools_used", [])
        successful_actions = session_context.get("successful_actions", [])
        
        summary_parts = ["Progress summary:"]
        
        if tools_used:
            summary_parts.append(f"- Tools used: {', '.join(set(tools_used))}")
        
        if successful_actions:
            summary_parts.append("- Completed actions:")
            for action in successful_actions[-5:]:  # Last 5
                summary_parts.append(f"  - {action}")
        
        summary_parts.append("\nPlease continue from here with a fresh approach.")
        
        guidance = "\n".join(summary_parts)
        
        return RecoveryAction(
            action_type=RecoveryActionType.SUMMARIZE,
            guidance_message=guidance,
            reason="Summarizing progress to break loop",
            escalation_level=escalation_level,
        )


class ProviderFallbackStrategy(RecoveryStrategy):
    """
    Switch to a different model as fallback.
    
    When the current model is stuck in a loop, sometimes a different
    model can break the pattern with different reasoning.
    """
    
    def __init__(self, fallback_models: Optional[List[str]] = None):
        self.fallback_models = fallback_models or [
            "gpt-4",
            "claude-3-sonnet",
            "gemini-pro",
        ]
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate model fallback action."""
        current_model = session_context.get("model_name", "")
        
        # Find a different model
        fallback = None
        for model in self.fallback_models:
            if model.lower() not in current_model.lower():
                fallback = model
                break
        
        if not fallback:
            # No alternative available, escalate to terminate
            return TerminateStrategy().get_action(
                loop_type, detail, escalation_level, session_context
            )
        
        guidance = (
            f"Switching to model '{fallback}' to break the detected loop. "
            f"The new model will continue from the current context."
        )
        
        return RecoveryAction(
            action_type=RecoveryActionType.SWITCH_MODEL,
            fallback_model=fallback,
            guidance_message=guidance,
            reason=f"Model fallback due to {loop_type}",
            escalation_level=escalation_level,
        )


class TerminateStrategy(RecoveryStrategy):
    """
    Terminate execution when recovery is not possible.
    
    This is the final escalation when all other strategies have failed.
    """
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Generate terminate action."""
        message = (
            "Execution has been stopped due to a persistent loop that could not be resolved. "
            f"Loop type: {loop_type}\n"
        )
        if detail:
            message += f"Detail: {detail}\n"
        
        message += (
            "\nTo continue, please:\n"
            "1. Rephrase your request more specifically\n"
            "2. Break down the task into smaller steps\n"
            "3. Provide additional context or constraints"
        )
        
        return RecoveryAction(
            action_type=RecoveryActionType.TERMINATE,
            guidance_message=message,
            reason=f"Terminating due to unrecoverable {loop_type}",
            escalation_level=escalation_level,
        )


class CompositeRecoveryStrategy(RecoveryStrategy):
    """
    Combines multiple strategies based on escalation level.
    
    Applies increasingly aggressive strategies as escalation increases.
    """
    
    def __init__(self):
        self.strategies_by_level = {
            RecoveryEscalationLevel.GUIDANCE: [
                RethinkStrategy(),
                SummarizeProgressStrategy(),
            ],
            RecoveryEscalationLevel.TOOL_COOLDOWN: [
                ToolCooldownStrategy(),
                ContextResetStrategy(),
            ],
            RecoveryEscalationLevel.TERMINATE: [
                ProviderFallbackStrategy(),
                TerminateStrategy(),
            ],
        }
    
    def get_action(
        self,
        loop_type: str,
        detail: Optional[str],
        escalation_level: RecoveryEscalationLevel,
        session_context: Dict[str, Any],
    ) -> RecoveryAction:
        """Get action based on escalation level."""
        strategies = self.strategies_by_level.get(escalation_level, [])
        
        if not strategies:
            return RecoveryAction(
                action_type=RecoveryActionType.NO_OP,
                reason="No strategy available for escalation level",
            )
        
        # For tool-related loops, prefer tool cooldown
        if "tool" in loop_type.lower() and escalation_level == RecoveryEscalationLevel.TOOL_COOLDOWN:
            for strategy in strategies:
                if isinstance(strategy, ToolCooldownStrategy):
                    return strategy.get_action(
                        loop_type, detail, escalation_level, session_context
                    )
        
        # Default to first strategy for the level
        return strategies[0].get_action(
            loop_type, detail, escalation_level, session_context
        )


def get_recovery_action(
    loop_type: str,
    detail: Optional[str],
    escalation_level: RecoveryEscalationLevel,
    session_context: Optional[Dict[str, Any]] = None,
) -> RecoveryAction:
    """
    Convenience function to get recovery action.
    
    Uses CompositeRecoveryStrategy by default.
    """
    strategy = CompositeRecoveryStrategy()
    return strategy.get_action(
        loop_type,
        detail,
        escalation_level,
        session_context or {},
    )
