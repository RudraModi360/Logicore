from typing import Optional, Literal
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class ThinkParams(BaseModel):
    thought: str = Field(
        ...,
        description="Your reasoning, analysis, or thought process"
    )
    conclusion: Optional[str] = Field(
        None,
        description="The conclusion reached after thinking"
    )
    depth: Optional[Literal["minimal", "low", "medium", "high", "deep"]] = Field(
        "medium",
        description="Reasoning depth level: 'minimal' (quick), 'low' (brief), 'medium' (standard), 'high' (thorough), 'deep' (exhaustive)"
    )
    analysis_type: Optional[Literal["problem", "solution", "tradeoff", "risk", "plan"]] = Field(
        None,
        description="Type of analysis: 'problem' (root cause), 'solution' (approaches), 'tradeoff' (pros/cons), 'risk' (identify risks), 'plan' (execution plan)"
    )


class ThinkTool(BaseTool):
    """
    A sophisticated tool for deep reasoning, strategic planning, and complex problem decomposition.
    Use this to architect multi-step solutions, analyze data dependencies, and identify 
    potential risks before executing actions.
    
    Enhanced with reasoning depth levels:
    - minimal: Quick assessment (1-2 considerations)
    - low: Brief analysis (few key points)
    - medium: Standard step-by-step reasoning
    - high: Thorough multi-perspective analysis
    - deep: Exhaustive exploration of all angles
    """
    
    name = "think"
    description = (
        "Advanced reasoning and planning tool with configurable depth. "
        "Use this for deep analysis, decomposing complex queries into actionable sub-tasks, "
        "and formulating a robust execution strategy before taking action. "
        "Set depth='deep' for exhaustive analysis, depth='minimal' for quick assessments."
    )
    args_schema = ThinkParams
    
    def run(
        self,
        thought: str,
        conclusion: str = None,
        depth: str = "medium",
        analysis_type: str = None,
        **kwargs
    ) -> ToolResult:
        depth_labels = {
            "minimal": "⚡ Quick Assessment",
            "low": "📝 Brief Analysis",
            "medium": "🧠 Step-by-Step Reasoning",
            "high": "🔬 Thorough Analysis",
            "deep": "🌊 Deep Dive Analysis",
        }
        
        type_headers = {
            "problem": "🔍 Problem Analysis",
            "solution": "💡 Solution Exploration",
            "tradeoff": "⚖️ Trade-off Analysis",
            "risk": "⚠️ Risk Assessment",
            "plan": "📋 Execution Planning",
        }
        
        depth_label = depth_labels.get(depth, depth_labels["medium"])
        
        lines = [f"### {depth_label}"]
        
        if analysis_type and analysis_type in type_headers:
            lines.append(f"**{type_headers[analysis_type]}**")
        
        lines.append("")
        lines.append(thought)
        
        if conclusion:
            lines.append("")
            lines.append("### 📋 Strategic Execution Roadmap")
            lines.append(conclusion)
        
        if depth == "deep":
            lines.append("")
            lines.append("---")
            lines.append("*Deep analysis complete. Consider edge cases and long-term implications before proceeding.*")
        elif depth == "high":
            lines.append("")
            lines.append("---")
            lines.append("*Thorough analysis complete. Validate assumptions before implementation.*")
            
        return ToolResult(
            success=True, 
            content="\n".join(lines)
        )
