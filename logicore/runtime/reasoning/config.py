"""
ReasoningConfig: Configurable reasoning levels and thinking budgets.

Inspired by gemini-cli's thinkingConfig architecture:
- thinkingBudget: Token budget for reasoning (0-8192)
- thinkingLevel: Depth level (HIGH/MEDIUM/LOW)

Python adaptation uses both native model thinking (where supported)
and system prompt injection for universal provider compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class ReasoningLevel(Enum):
    """
    Reasoning depth levels for agent execution.
    
    Maps to different system prompt behaviors and thinking budgets.
    Inspired by gemini-cli's ThinkingLevel enum.
    """
    MINIMAL = 1   # Quick responses, minimal analysis
    LOW = 2       # Brief reasoning, 1-2 steps
    MEDIUM = 3    # Standard step-by-step reasoning (default)
    HIGH = 4      # Deep analysis, multiple perspectives
    DEEP = 5      # Exhaustive analysis, extended thinking, explore all angles


class ApprovalMode(Enum):
    """
    Approval modes that indirectly control reasoning depth.
    
    Inspired by gemini-cli's policy approval modes.
    """
    PLAN = "plan"         # Requires explicit planning before execution
    DEFAULT = "default"   # Standard reasoning with approval for dangerous ops
    AUTO_EDIT = "auto"    # Faster reasoning, auto-approve safe edits
    YOLO = "yolo"         # Minimal reasoning, approve everything


@dataclass
class ReasoningConfig:
    """
    Configuration for agent reasoning behavior.
    
    Attributes:
        level: Reasoning depth level (MINIMAL to DEEP)
        thinking_budget: Maximum tokens for reasoning/thinking (0 = unlimited)
        include_thoughts: Whether to capture and display thinking process
        show_reasoning_steps: Show step-by-step reasoning to user
        auto_escalate: Automatically increase reasoning level for complex tasks
        auto_escalate_keywords: Keywords that trigger auto-escalation
        approval_mode: Approval mode controlling execution style
    """
    
    # Core reasoning settings
    level: ReasoningLevel = ReasoningLevel.MEDIUM
    thinking_budget: int = 2048  # Token budget (0 = unlimited, 8192 = max for most models)
    include_thoughts: bool = True
    show_reasoning_steps: bool = False
    
    # Auto-escalation settings
    auto_escalate: bool = True
    auto_escalate_keywords: list = field(default_factory=lambda: [
        "complex", "difficult", "analyze", "investigate", "debug",
        "architecture", "design", "plan", "strategy", "optimize",
        "refactor", "security", "performance", "multi-step",
        "comprehensive", "thorough", "deep dive", "root cause"
    ])
    
    # Approval mode
    approval_mode: ApprovalMode = ApprovalMode.DEFAULT
    
    def should_escalate(self, query: str) -> bool:
        """Check if query should trigger reasoning escalation."""
        if not self.auto_escalate:
            return False
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in self.auto_escalate_keywords)
    
    def get_escalated_level(self) -> ReasoningLevel:
        """Get the escalated reasoning level."""
        level_order = [
            ReasoningLevel.MINIMAL,
            ReasoningLevel.LOW,
            ReasoningLevel.MEDIUM,
            ReasoningLevel.HIGH,
            ReasoningLevel.DEEP
        ]
        current_idx = level_order.index(self.level)
        # Escalate by one level, max is DEEP
        new_idx = min(current_idx + 1, len(level_order) - 1)
        return level_order[new_idx]
    
    def get_thinking_budget_for_level(self) -> int:
        """Get recommended thinking budget based on level."""
        budget_map = {
            ReasoningLevel.MINIMAL: 256,
            ReasoningLevel.LOW: 512,
            ReasoningLevel.MEDIUM: 2048,
            ReasoningLevel.HIGH: 4096,
            ReasoningLevel.DEEP: 8192,
        }
        return self.thinking_budget or budget_map.get(self.level, 2048)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "level": self.level.name,
            "thinking_budget": self.thinking_budget,
            "include_thoughts": self.include_thoughts,
            "show_reasoning_steps": self.show_reasoning_steps,
            "auto_escalate": self.auto_escalate,
            "approval_mode": self.approval_mode.value,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReasoningConfig":
        """Create from dictionary."""
        return cls(
            level=ReasoningLevel[data.get("level", "MEDIUM")],
            thinking_budget=data.get("thinking_budget", 2048),
            include_thoughts=data.get("include_thoughts", True),
            show_reasoning_steps=data.get("show_reasoning_steps", False),
            auto_escalate=data.get("auto_escalate", True),
            approval_mode=ApprovalMode(data.get("approval_mode", "default")),
        )


# Preset configurations for common use cases
REASONING_PRESETS: Dict[str, ReasoningConfig] = {
    "quick": ReasoningConfig(
        level=ReasoningLevel.MINIMAL,
        thinking_budget=256,
        include_thoughts=False,
        auto_escalate=False,
        approval_mode=ApprovalMode.AUTO_EDIT,
    ),
    "standard": ReasoningConfig(
        level=ReasoningLevel.MEDIUM,
        thinking_budget=2048,
        include_thoughts=True,
        auto_escalate=True,
        approval_mode=ApprovalMode.DEFAULT,
    ),
    "thorough": ReasoningConfig(
        level=ReasoningLevel.HIGH,
        thinking_budget=4096,
        include_thoughts=True,
        show_reasoning_steps=True,
        auto_escalate=True,
        approval_mode=ApprovalMode.DEFAULT,
    ),
    "deep_analysis": ReasoningConfig(
        level=ReasoningLevel.DEEP,
        thinking_budget=8192,
        include_thoughts=True,
        show_reasoning_steps=True,
        auto_escalate=False,  # Already at max
        approval_mode=ApprovalMode.PLAN,
    ),
    "yolo": ReasoningConfig(
        level=ReasoningLevel.LOW,
        thinking_budget=512,
        include_thoughts=False,
        auto_escalate=False,
        approval_mode=ApprovalMode.YOLO,
    ),
}


def get_reasoning_system_prompt_addon(config: ReasoningConfig) -> str:
    """
    Generate system prompt addon based on reasoning configuration.
    
    This is injected into the system prompt to guide the model's reasoning behavior
    when native thinking budget isn't supported by the provider.
    """
    level_prompts = {
        ReasoningLevel.MINIMAL: """
## Reasoning Approach: Minimal
- Provide brief, direct answers without extensive analysis
- Skip detailed explanations unless specifically requested
- Focus on the most immediate and relevant solution
- Limit reasoning to 1-2 quick considerations
""",
        ReasoningLevel.LOW: """
## Reasoning Approach: Concise
- Provide concise reasoning with 1-2 key steps
- Focus on the primary solution path
- Brief justification for decisions
- Skip edge case analysis unless critical
""",
        ReasoningLevel.MEDIUM: """
## Reasoning Approach: Standard
- Apply step-by-step reasoning for problem analysis
- Consider main alternatives before deciding
- Provide clear justification for chosen approach
- Identify potential issues but stay focused
- Balance thoroughness with efficiency
""",
        ReasoningLevel.HIGH: """
## Reasoning Approach: Thorough
- Conduct deep analysis with multiple perspectives
- Explore alternative approaches systematically
- Consider edge cases and potential pitfalls
- Provide detailed justification for decisions
- Think through implications and dependencies
- Validate assumptions before proceeding
""",
        ReasoningLevel.DEEP: """
## Reasoning Approach: Exhaustive
- Perform exhaustive analysis exploring all angles
- Extended thinking before taking any action
- Systematically evaluate all viable approaches
- Deep investigation of root causes
- Consider long-term implications and maintainability
- Question assumptions and verify understanding
- Document reasoning process comprehensively
- Seek clarification when requirements are ambiguous
- Build execution plan before implementation
""",
    }
    
    base_prompt = level_prompts.get(config.level, level_prompts[ReasoningLevel.MEDIUM])
    
    # Add approval mode context
    if config.approval_mode == ApprovalMode.PLAN:
        base_prompt += """
### Planning Required
Before executing any significant changes, create a detailed plan and present it for approval.
Break down complex tasks into clear steps. Wait for confirmation before proceeding.
"""
    elif config.approval_mode == ApprovalMode.YOLO:
        base_prompt += """
### Fast Execution Mode
Proceed directly with implementation. Minimize questions and confirmations.
Prioritize speed over extensive validation.
"""
    
    # Add thinking visibility
    if config.show_reasoning_steps:
        base_prompt += """
### Visible Reasoning
Show your thinking process step-by-step using the think tool.
Make your reasoning transparent to help users understand your approach.
"""
    
    return base_prompt.strip()
