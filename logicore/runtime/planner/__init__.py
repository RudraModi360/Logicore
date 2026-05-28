"""
Plan Service Module

Provides plan-before-execute workflow with approval gates.
Inspired by gemini-cli's enter-plan-mode/exit-plan-mode pattern.

Components:
- Plan: Plan data model with steps
- PlanStep: Individual step in a plan
- PlanService: CRUD and approval workflow

Usage:
    from logicore.runtime.planner import PlanService, PlanStatus
    
    planner = PlanService()
    
    # Create and submit plan
    plan = planner.create_plan("Implement feature", [
        {"description": "Create models", "order": 1},
        {"description": "Add API routes", "order": 2},
        {"description": "Write tests", "order": 3},
    ])
    
    # Approve and execute
    planner.approve_plan(plan.id)
    planner.update_step_status(plan.id, step_id, "completed")
"""

from logicore.runtime.planner.service import (
    Plan,
    PlanStep,
    PlanStatus,
    StepStatus,
    PlanService,
)

__all__ = [
    "Plan",
    "PlanStep",
    "PlanStatus",
    "StepStatus",
    "PlanService",
]
