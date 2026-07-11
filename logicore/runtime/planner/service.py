"""
PlanService: Plan-before-execute workflow with approval gates.

Adapted from gemini-cli's enter-plan-mode/exit-plan-mode pattern.
Provides structured planning with user approval before execution.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable


class PlanStatus(Enum):
    """Plan lifecycle states."""
    DRAFT = "draft"           # Being created
    PENDING = "pending"       # Awaiting approval
    APPROVED = "approved"     # Ready for execution
    IN_PROGRESS = "in_progress"  # Currently executing
    COMPLETED = "completed"   # All steps done
    REJECTED = "rejected"     # User rejected
    CANCELLED = "cancelled"   # Cancelled during execution


class StepStatus(Enum):
    """Individual step states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class PlanStep:
    """
    Individual step in a plan.
    
    Attributes:
        id: Unique step identifier
        description: What this step accomplishes
        order: Execution order (1-indexed)
        status: Current step status
        dependencies: Step IDs that must complete first
        estimated_turns: Estimated turns to complete
        actual_turns: Actual turns taken
        notes: Progress notes
        started_at: When step started
        completed_at: When step completed
    """
    
    id: str
    description: str
    order: int
    status: StepStatus = StepStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    estimated_turns: int = 1
    actual_turns: int = 0
    notes: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:6]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "order": self.order,
            "status": self.status.value,
            "dependencies": self.dependencies,
            "estimated_turns": self.estimated_turns,
            "actual_turns": self.actual_turns,
            "notes": self.notes,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        return cls(
            id=data["id"],
            description=data["description"],
            order=data["order"],
            status=StepStatus(data.get("status", "pending")),
            dependencies=data.get("dependencies", []),
            estimated_turns=data.get("estimated_turns", 1),
            actual_turns=data.get("actual_turns", 0),
            notes=data.get("notes", []),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
        )


@dataclass
class Plan:
    """
    Execution plan with multiple steps.
    
    Attributes:
        id: Unique plan identifier
        title: Plan title/summary
        description: Detailed plan description
        status: Current plan status
        steps: List of plan steps
        reason: Why this plan was created
        created_at: Creation timestamp
        approved_at: Approval timestamp
        completed_at: Completion timestamp
        rejection_reason: Why plan was rejected (if applicable)
        metadata: Arbitrary metadata
    """
    
    id: str
    title: str
    description: str = ""
    status: PlanStatus = PlanStatus.DRAFT
    steps: List[PlanStep] = field(default_factory=list)
    reason: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    approved_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
    
    @property
    def progress_percent(self) -> int:
        """Calculate overall progress percentage."""
        if not self.steps:
            return 0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return int((completed / len(self.steps)) * 100)
    
    @property
    def current_step(self) -> Optional[PlanStep]:
        """Get the current step being executed."""
        for step in sorted(self.steps, key=lambda s: s.order):
            if step.status in [StepStatus.PENDING, StepStatus.IN_PROGRESS]:
                return step
        return None
    
    @property
    def is_complete(self) -> bool:
        """Check if all steps are completed."""
        return all(
            s.status in [StepStatus.COMPLETED, StepStatus.SKIPPED]
            for s in self.steps
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "rejection_reason": self.rejection_reason,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=PlanStatus(data.get("status", "draft")),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            reason=data.get("reason", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else datetime.now(),
            approved_at=datetime.fromisoformat(data["approved_at"]) if data.get("approved_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            rejection_reason=data.get("rejection_reason"),
            metadata=data.get("metadata", {}),
        )
    
    def __str__(self) -> str:
        status_icons = {
            PlanStatus.DRAFT: "📝",
            PlanStatus.PENDING: "⏳",
            PlanStatus.APPROVED: "✅",
            PlanStatus.IN_PROGRESS: "🔄",
            PlanStatus.COMPLETED: "✔️",
            PlanStatus.REJECTED: "❌",
            PlanStatus.CANCELLED: "🚫",
        }
        icon = status_icons.get(self.status, "?")
        return f"{icon} Plan [{self.id}]: {self.title} ({self.progress_percent}%)"


class PlanService:
    """
    Service for managing execution plans with approval workflow.
    
    Features:
    - Create multi-step plans
    - Submit for user approval
    - Track step execution
    - Persist plans to disk
    
    Usage:
        planner = PlanService(project_dir="/path/to/project")
        
        # Enter plan mode
        plan = planner.create_plan("Refactor module", [
            {"description": "Analyze current structure"},
            {"description": "Create new interfaces"},
            {"description": "Migrate implementation"},
            {"description": "Update tests"},
        ])
        
        # Submit and wait for approval
        planner.submit_plan(plan.id)
        
        # After approval, execute
        planner.start_step(plan.id, step_id)
        planner.complete_step(plan.id, step_id)
    """
    
    def __init__(
        self,
        project_dir: Optional[str] = None,
        auto_save: bool = True,
        approval_callback: Optional[Callable[[Plan], bool]] = None,
    ):
        """
        Initialize plan service.
        
        Args:
            project_dir: Project directory for persistence
            auto_save: Automatically save after modifications
            approval_callback: Optional callback for approval (returns True/False)
        """
        # Plans persist under the config-controlled root, never the cwd.
        from logicore.config import settings
        self.project_dir = Path(project_dir) if project_dir else settings.paths.storage_root
        self.plans_dir = settings.paths.plans_dir
        self.auto_save = auto_save
        self.approval_callback = approval_callback
        
        self._plans: Dict[str, Plan] = {}
        self._current_plan_id: Optional[str] = None
        self._load_all()
    
    def _ensure_dir(self) -> None:
        """Ensure plans directory exists."""
        self.plans_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_plan_file(self, plan_id: str) -> Path:
        """Get path to plan file."""
        return self.plans_dir / f"{plan_id}.json"
    
    def _load_all(self) -> None:
        """Load all plans from disk."""
        if not self.plans_dir.exists():
            return
        
        for plan_file in self.plans_dir.glob("*.json"):
            try:
                with open(plan_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plan = Plan.from_dict(data)
                self._plans[plan.id] = plan
            except (json.JSONDecodeError, KeyError):
                pass  # Skip corrupted files
    
    def _save_plan(self, plan: Plan) -> None:
        """Save a plan to disk."""
        self._ensure_dir()
        with open(self._get_plan_file(plan.id), "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, indent=2)
    
    def _maybe_save(self, plan: Plan) -> None:
        """Save if auto_save is enabled."""
        if self.auto_save:
            self._save_plan(plan)
    
    @property
    def current_plan(self) -> Optional[Plan]:
        """Get the currently active plan."""
        if self._current_plan_id:
            return self._plans.get(self._current_plan_id)
        return None
    
    @property
    def is_in_plan_mode(self) -> bool:
        """Check if currently in plan mode."""
        return self._current_plan_id is not None
    
    # ========== CRUD Operations ==========
    
    def create_plan(
        self,
        title: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        """
        Create a new plan.
        
        Args:
            title: Plan title
            steps: List of step dicts with 'description', optional 'order', 'estimated_turns'
            description: Detailed plan description
            reason: Why this plan is being created
            metadata: Optional metadata
            
        Returns:
            Created Plan
        """
        plan_steps = []
        for i, step_data in enumerate(steps, 1):
            plan_steps.append(PlanStep(
                id=uuid.uuid4().hex[:6],
                description=step_data.get("description", f"Step {i}"),
                order=step_data.get("order", i),
                estimated_turns=step_data.get("estimated_turns", 1),
                dependencies=step_data.get("dependencies", []),
            ))
        
        plan = Plan(
            id=uuid.uuid4().hex[:8],
            title=title,
            description=description,
            reason=reason,
            steps=plan_steps,
            metadata=metadata or {},
        )
        
        self._plans[plan.id] = plan
        self._maybe_save(plan)
        return plan
    
    def get_plan(self, plan_id: str) -> Plan:
        """Get a plan by ID."""
        if plan_id not in self._plans:
            raise ValueError(f"Plan '{plan_id}' not found")
        return self._plans[plan_id]
    
    def list_plans(
        self,
        status: Optional[PlanStatus] = None,
        include_completed: bool = False,
    ) -> List[Plan]:
        """List plans with optional filtering."""
        plans = list(self._plans.values())
        
        if status:
            plans = [p for p in plans if p.status == status]
        
        if not include_completed:
            plans = [p for p in plans if p.status not in [PlanStatus.COMPLETED, PlanStatus.REJECTED, PlanStatus.CANCELLED]]
        
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans
    
    def delete_plan(self, plan_id: str) -> None:
        """Delete a plan."""
        if plan_id not in self._plans:
            raise ValueError(f"Plan '{plan_id}' not found")
        
        del self._plans[plan_id]
        
        # Remove file
        plan_file = self._get_plan_file(plan_id)
        if plan_file.exists():
            plan_file.unlink()
        
        if self._current_plan_id == plan_id:
            self._current_plan_id = None
    
    # ========== Plan Mode Workflow ==========
    
    def enter_plan_mode(self, reason: str = "") -> str:
        """
        Enter plan mode (agent signals need for planning).
        
        Returns:
            Message indicating plan mode is active
        """
        return (
            f"📋 Entering plan mode.\n"
            f"Reason: {reason or 'Complex task requires planning'}\n\n"
            f"Please create a plan with steps using submit_plan."
        )
    
    def submit_plan(self, plan_id: str) -> Plan:
        """
        Submit a plan for approval.
        
        Args:
            plan_id: Plan to submit
            
        Returns:
            Updated plan with PENDING status
        """
        plan = self.get_plan(plan_id)
        plan.status = PlanStatus.PENDING
        self._current_plan_id = plan_id
        self._maybe_save(plan)
        return plan
    
    def approve_plan(self, plan_id: str) -> Plan:
        """
        Approve a plan for execution.
        
        Args:
            plan_id: Plan to approve
            
        Returns:
            Approved plan
        """
        plan = self.get_plan(plan_id)
        plan.status = PlanStatus.APPROVED
        plan.approved_at = datetime.now()
        self._current_plan_id = plan_id
        self._maybe_save(plan)
        return plan
    
    def reject_plan(self, plan_id: str, reason: str = "") -> Plan:
        """
        Reject a plan.
        
        Args:
            plan_id: Plan to reject
            reason: Rejection reason
            
        Returns:
            Rejected plan
        """
        plan = self.get_plan(plan_id)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = reason
        self._current_plan_id = None
        self._maybe_save(plan)
        return plan
    
    def exit_plan_mode(self, plan_id: Optional[str] = None) -> str:
        """
        Exit plan mode and resume normal execution.
        
        Args:
            plan_id: Optional plan to mark as complete
            
        Returns:
            Exit message
        """
        if plan_id:
            plan = self.get_plan(plan_id)
            if plan.is_complete:
                plan.status = PlanStatus.COMPLETED
                plan.completed_at = datetime.now()
                self._maybe_save(plan)
        
        self._current_plan_id = None
        return "📋 Exiting plan mode. Resuming normal execution."
    
    # ========== Step Operations ==========
    
    def start_step(self, plan_id: str, step_id: str) -> PlanStep:
        """Mark a step as in progress."""
        plan = self.get_plan(plan_id)
        
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.IN_PROGRESS
                step.started_at = datetime.now()
                plan.status = PlanStatus.IN_PROGRESS
                self._maybe_save(plan)
                return step
        
        raise ValueError(f"Step '{step_id}' not found in plan '{plan_id}'")
    
    def complete_step(self, plan_id: str, step_id: str, note: str = "") -> PlanStep:
        """Mark a step as completed."""
        plan = self.get_plan(plan_id)
        
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.COMPLETED
                step.completed_at = datetime.now()
                if note:
                    step.notes.append(note)
                
                # Check if plan is complete
                if plan.is_complete:
                    plan.status = PlanStatus.COMPLETED
                    plan.completed_at = datetime.now()
                
                self._maybe_save(plan)
                return step
        
        raise ValueError(f"Step '{step_id}' not found in plan '{plan_id}'")
    
    def skip_step(self, plan_id: str, step_id: str, reason: str = "") -> PlanStep:
        """Skip a step."""
        plan = self.get_plan(plan_id)
        
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.SKIPPED
                if reason:
                    step.notes.append(f"Skipped: {reason}")
                self._maybe_save(plan)
                return step
        
        raise ValueError(f"Step '{step_id}' not found in plan '{plan_id}'")
    
    def fail_step(self, plan_id: str, step_id: str, error: str = "") -> PlanStep:
        """Mark a step as failed."""
        plan = self.get_plan(plan_id)
        
        for step in plan.steps:
            if step.id == step_id:
                step.status = StepStatus.FAILED
                if error:
                    step.notes.append(f"Failed: {error}")
                self._maybe_save(plan)
                return step
        
        raise ValueError(f"Step '{step_id}' not found in plan '{plan_id}'")
    
    # ========== Visualization ==========
    
    def visualize_plan(self, plan_id: str) -> str:
        """Generate a text visualization of a plan."""
        plan = self.get_plan(plan_id)
        
        lines = [
            str(plan),
            "=" * 50,
            f"Description: {plan.description or '(none)'}",
            f"Status: {plan.status.value}",
            f"Progress: {plan.progress_percent}%",
            "",
            "Steps:",
        ]
        
        step_icons = {
            StepStatus.PENDING: "○",
            StepStatus.IN_PROGRESS: "◐",
            StepStatus.COMPLETED: "●",
            StepStatus.SKIPPED: "⊘",
            StepStatus.FAILED: "✗",
        }
        
        for step in sorted(plan.steps, key=lambda s: s.order):
            icon = step_icons.get(step.status, "?")
            lines.append(f"  {step.order}. {icon} {step.description}")
            if step.notes:
                for note in step.notes[-2:]:  # Last 2 notes
                    lines.append(f"       └─ {note}")
        
        return "\n".join(lines)
    
    def get_progress_summary(self, plan_id: str) -> Dict[str, Any]:
        """Get progress summary for telemetry."""
        plan = self.get_plan(plan_id)
        
        return {
            "plan_id": plan.id,
            "title": plan.title,
            "status": plan.status.value,
            "progress_percent": plan.progress_percent,
            "total_steps": len(plan.steps),
            "completed_steps": sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
            "current_step": plan.current_step.description if plan.current_step else None,
        }
