"""
Verification Tool for self-reporting plan execution status.

Based on Claude Code's VerifyPlanExecutionTool pattern:
- LLM-invoked tool (not automatic)
- Structured self-reporting mechanism
- Reports verification status
- No actual verification (model tells us what it verified)

Key insight from Claude Code:
- VerifyPlanExecutionTool is invoked by the LLM, not automatically
- The tool simply passes through the model's self-assessment
- A verify_plan_reminder attachment periodically nudges the model to call it
"""

from typing import Optional
from pydantic import BaseModel, Field

from logicore.tools.base import BaseTool, ToolResult


class VerifyPlanExecutionParams(BaseModel):
    """Arguments for verify_plan_execution tool."""
    plan_summary: str = Field(
        description="Summary of the executed plan"
    )
    all_steps_completed: bool = Field(
        description="Whether all plan steps were completed successfully"
    )
    verification_notes: Optional[str] = Field(
        default=None,
        description="What was verified, issues found, and confidence level"
    )


class VerifyPlanExecutionTool(BaseTool):
    """
    Verification tool for self-reporting plan execution status.
    
    Based on Claude Code's VerifyPlanExecutionTool:
    - LLM calls this to report verification status
    - Tool simply passes through the model's self-assessment
    - No actual verification logic (model tells us what it verified)
    
    Usage:
    - Model calls this after executing a plan
    - Reports whether all steps were completed
    - Provides verification notes
    """
    
    name = "verify_plan_execution"
    description = (
        "Verify that a plan was executed correctly before exiting plan mode. "
        "Call this tool before exiting plan mode to confirm all steps were completed.\n\n"
        "Guidelines:\n"
        "- Summarize the plan that was executed\n"
        "- Note whether all steps completed successfully\n"
        "- Include any verification notes (tests passed, files created, etc.)\n"
        "- If steps were skipped or failed, explain why in verification_notes"
    )
    args_schema = VerifyPlanExecutionParams
    
    def is_read_only(self, args=None) -> bool:
        """Verification is read-only (no side effects)."""
        return True
    
    def is_destructive(self, args=None) -> bool:
        """Verification is NOT destructive."""
        return False
    
    def is_concurrency_safe(self, args=None) -> bool:
        """Verification is concurrency-safe (read-only operation)."""
        return True
    
    def run(
        self,
        plan_summary: str,
        all_steps_completed: bool,
        verification_notes: Optional[str] = None,
        **kwargs
    ) -> ToolResult:
        """
        Execute the verification tool.
        
        Based on Claude Code's VerifyPlanExecutionTool.call():
        - Simply passes through the model's self-assessment
        - Returns verified status based on all_steps_completed
        """
        if all_steps_completed:
            return ToolResult(
                success=True,
                content={
                    "verified": True,
                    "summary": plan_summary,
                    "notes": verification_notes or "All steps completed successfully"
                }
            )
        else:
            return ToolResult(
                success=True,
                content={
                    "verified": False,
                    "summary": plan_summary,
                    "notes": verification_notes or "Not all steps completed",
                    "recommendation": "Review incomplete steps and retry or ask for help"
                }
            )


# Tool registration
def get_verify_tools():
    """Get all verification tools."""
    return [
        VerifyPlanExecutionTool(),
    ]


def get_verify_tool_schemas():
    """Get schemas for all verification tools."""
    return [tool.schema for tool in get_verify_tools()]
