"""
Plan Tools: Agent tools for plan mode operations.

Provides tools for agents to enter plan mode, submit plans,
and track plan execution. Adapted from gemini-cli's plan mode tools.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from logicore.tools.base import BaseTool, ToolResult
from logicore.runtime.planner import (
    PlanService,
    Plan,
    PlanStep,
    PlanStatus,
    StepStatus,
)


# Global planner instance (lazily initialized per project)
_planner_instance: Optional[PlanService] = None


def get_planner(project_dir: Optional[str] = None) -> PlanService:
    """Get or create planner instance."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = PlanService(project_dir=project_dir)
    return _planner_instance


def set_planner(planner: PlanService) -> None:
    """Set the planner instance (for dependency injection)."""
    global _planner_instance
    _planner_instance = planner


# ============== Enter Plan Mode Tool ==============

class EnterPlanModeParams(BaseModel):
    reason: Optional[str] = Field(
        None,
        description="Reason for entering plan mode (explains why planning is needed)"
    )


class EnterPlanModeTool(BaseTool):
    """Enter plan mode for complex multi-step tasks."""
    
    name = "enter_plan_mode"
    description = (
        "Enter plan mode when facing a complex task that requires careful planning. "
        "Use for: multi-step implementations, architectural changes, "
        "tasks with dependencies, anything that needs user approval before execution. "
        "After entering, create a plan with steps and submit for approval."
    )
    args_schema = EnterPlanModeParams
    
    def run(self, reason: str = None, **kwargs) -> ToolResult:
        try:
            planner = get_planner()
            message = planner.enter_plan_mode(reason=reason or "")
            return ToolResult(success=True, content=message)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Submit Plan Tool ==============

class SubmitPlanParams(BaseModel):
    title: str = Field(
        ...,
        description="Brief plan title describing the goal"
    )
    steps: List[str] = Field(
        ...,
        description="List of step descriptions in execution order"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed plan description"
    )
    reason: Optional[str] = Field(
        None,
        description="Why this plan was created"
    )


class SubmitPlanTool(BaseTool):
    """Create and submit a plan for user approval."""
    
    name = "submit_plan"
    description = (
        "Create a multi-step plan and submit for user approval. "
        "Use after enter_plan_mode to define the execution steps. "
        "User must approve before execution proceeds."
    )
    args_schema = SubmitPlanParams
    
    def run(
        self,
        title: str,
        steps: List[str],
        description: str = None,
        reason: str = None,
        **kwargs
    ) -> ToolResult:
        try:
            planner = get_planner()
            
            # Convert step strings to step dicts
            step_dicts = [{"description": step} for step in steps]
            
            # Create plan
            plan = planner.create_plan(
                title=title,
                steps=step_dicts,
                description=description or "",
                reason=reason or "",
            )
            
            # Submit for approval
            planner.submit_plan(plan.id)
            
            # Generate preview
            lines = [
                f"📋 Plan submitted for approval: {plan.title}",
                f"Plan ID: {plan.id}",
                "",
                "Steps:",
            ]
            for i, step in enumerate(plan.steps, 1):
                lines.append(f"  {i}. {step.description}")
            
            lines.append("")
            lines.append("⏳ Awaiting user approval...")
            lines.append("Use 'exit_plan_mode' after approval to begin execution.")
            
            return ToolResult(success=True, content="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Exit Plan Mode Tool ==============

class ExitPlanModeParams(BaseModel):
    plan_id: Optional[str] = Field(
        None,
        description="Plan ID to finalize (current plan if not specified)"
    )
    action: Optional[str] = Field(
        "execute",
        description="Action: 'execute' (proceed with plan), 'cancel' (abort plan)"
    )


class ExitPlanModeTool(BaseTool):
    """Exit plan mode and proceed with execution."""
    
    name = "exit_plan_mode"
    description = (
        "Exit plan mode after plan approval. "
        "Use with action='execute' to proceed with the approved plan, "
        "or action='cancel' to abort."
    )
    args_schema = ExitPlanModeParams
    
    def run(
        self,
        plan_id: str = None,
        action: str = "execute",
        **kwargs
    ) -> ToolResult:
        try:
            planner = get_planner()
            
            # Get current plan if not specified
            if not plan_id and planner.current_plan:
                plan_id = planner.current_plan.id
            
            if not plan_id:
                return ToolResult(
                    success=False,
                    error="No active plan. Create a plan first with submit_plan."
                )
            
            plan = planner.get_plan(plan_id)
            
            if action == "cancel":
                planner.reject_plan(plan_id, reason="Cancelled by agent")
                message = planner.exit_plan_mode()
                return ToolResult(
                    success=True,
                    content=f"❌ Plan cancelled.\n{message}"
                )
            
            # Approve and proceed
            if plan.status == PlanStatus.PENDING:
                planner.approve_plan(plan_id)
            
            message = planner.exit_plan_mode(plan_id)
            
            lines = [
                f"✅ Plan approved and ready for execution.",
                f"Plan: {plan.title}",
                "",
                "Executing steps:",
            ]
            for i, step in enumerate(plan.steps, 1):
                lines.append(f"  {i}. {step.description}")
            
            lines.append("")
            lines.append(message)
            
            return ToolResult(success=True, content="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Update Plan Progress Tool ==============

class UpdatePlanProgressParams(BaseModel):
    plan_id: Optional[str] = Field(
        None,
        description="Plan ID (current plan if not specified)"
    )
    step_number: int = Field(
        ...,
        ge=1,
        description="Step number to update (1-indexed)"
    )
    status: str = Field(
        ...,
        description="New status: 'in_progress', 'completed', 'skipped', 'failed'"
    )
    note: Optional[str] = Field(
        None,
        description="Progress note"
    )


class UpdatePlanProgressTool(BaseTool):
    """Update progress on a plan step."""
    
    name = "update_plan_progress"
    description = (
        "Update the status of a plan step. "
        "Use to mark steps as in_progress, completed, skipped, or failed. "
        "Automatically tracks plan completion."
    )
    args_schema = UpdatePlanProgressParams
    
    def run(
        self,
        step_number: int,
        status: str,
        plan_id: str = None,
        note: str = None,
        **kwargs
    ) -> ToolResult:
        try:
            planner = get_planner()
            
            # Get current plan if not specified
            if not plan_id and planner.current_plan:
                plan_id = planner.current_plan.id
            
            if not plan_id:
                return ToolResult(
                    success=False,
                    error="No active plan."
                )
            
            plan = planner.get_plan(plan_id)
            
            # Find step by number
            step = None
            for s in plan.steps:
                if s.order == step_number:
                    step = s
                    break
            
            if not step:
                return ToolResult(
                    success=False,
                    error=f"Step {step_number} not found in plan."
                )
            
            # Update step status
            if status == "in_progress":
                planner.start_step(plan_id, step.id)
            elif status == "completed":
                planner.complete_step(plan_id, step.id, note=note or "")
            elif status == "skipped":
                planner.skip_step(plan_id, step.id, reason=note or "")
            elif status == "failed":
                planner.fail_step(plan_id, step.id, error=note or "")
            else:
                return ToolResult(
                    success=False,
                    error=f"Invalid status: {status}"
                )
            
            # Refresh plan
            plan = planner.get_plan(plan_id)
            
            return ToolResult(
                success=True,
                content=f"Step {step_number} updated to '{status}'.\nPlan progress: {plan.progress_percent}%"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== View Plan Tool ==============

class ViewPlanParams(BaseModel):
    plan_id: Optional[str] = Field(
        None,
        description="Plan ID to view (current plan if not specified)"
    )


class ViewPlanTool(BaseTool):
    """View current plan status and progress."""
    
    name = "view_plan"
    description = (
        "View the current plan's status, steps, and progress. "
        "Use to check execution progress and remaining steps."
    )
    args_schema = ViewPlanParams
    
    def run(self, plan_id: str = None, **kwargs) -> ToolResult:
        try:
            planner = get_planner()
            
            # Get current plan if not specified
            if not plan_id and planner.current_plan:
                plan_id = planner.current_plan.id
            
            if not plan_id:
                # List recent plans
                plans = planner.list_plans(include_completed=False)
                if not plans:
                    return ToolResult(
                        success=True,
                        content="No active plans. Use enter_plan_mode to create one."
                    )
                
                lines = ["📋 Recent Plans:", ""]
                for plan in plans[:5]:
                    lines.append(str(plan))
                
                return ToolResult(success=True, content="\n".join(lines))
            
            visualization = planner.visualize_plan(plan_id)
            return ToolResult(success=True, content=visualization)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Tool Registration ==============

def get_plan_tools() -> List[BaseTool]:
    """Get all plan mode tools."""
    return [
        EnterPlanModeTool(),
        SubmitPlanTool(),
        ExitPlanModeTool(),
        UpdatePlanProgressTool(),
        ViewPlanTool(),
    ]


def get_plan_tool_schemas() -> List[Dict[str, Any]]:
    """Get schemas for all plan mode tools."""
    return [tool.schema for tool in get_plan_tools()]
