"""
ProgressService: Real-time progress tracking for agent execution.

Provides:
- Task-level progress tracking
- Step-by-step updates
- Time estimation
- Progress event emission for UI integration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any, Callable
import time


class ProgressEventType(Enum):
    """Types of progress events."""
    TASK_START = "task_start"
    STEP_UPDATE = "step_update"
    STEP_COMPLETE = "step_complete"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    PROGRESS_UPDATE = "progress_update"


@dataclass
class ProgressEvent:
    """Progress event for UI/telemetry consumption."""
    
    type: ProgressEventType
    task_id: str
    message: str
    progress_percent: int
    current_step: int
    total_steps: int
    elapsed_seconds: float
    estimated_remaining_seconds: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "task_id": self.task_id,
            "message": self.message,
            "progress_percent": self.progress_percent,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "elapsed_seconds": self.elapsed_seconds,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ProgressState:
    """Current progress state."""
    
    task_id: str
    task_title: str
    current_step: int = 0
    total_steps: int = 0
    progress_percent: int = 0
    current_message: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    step_times: List[float] = field(default_factory=list)
    is_complete: bool = False
    is_failed: bool = False
    error_message: Optional[str] = None
    
    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        return (datetime.now() - self.started_at).total_seconds()
    
    @property
    def avg_step_time(self) -> float:
        """Get average time per step."""
        if not self.step_times:
            return 0.0
        return sum(self.step_times) / len(self.step_times)
    
    @property
    def estimated_remaining_seconds(self) -> Optional[float]:
        """Estimate remaining time based on average step time."""
        if not self.step_times or self.current_step >= self.total_steps:
            return None
        remaining_steps = self.total_steps - self.current_step
        return remaining_steps * self.avg_step_time


class ProgressService:
    """
    Service for tracking and reporting progress during agent execution.
    
    Features:
    - Task and step-level tracking
    - Progress percentage calculation
    - Time estimation
    - Event callbacks for UI integration
    
    Usage:
        progress = ProgressService()
        
        # Register callback for UI updates
        progress.on_progress(lambda event: print(event.message))
        
        # Track execution
        progress.start_task("Building feature", total_steps=5)
        progress.update(1, "Step 1 complete")
        progress.update(2, "Step 2 complete")
        progress.complete()
    """
    
    def __init__(self):
        """Initialize progress service."""
        self._state: Optional[ProgressState] = None
        self._callbacks: List[Callable[[ProgressEvent], None]] = []
        self._step_start_time: Optional[float] = None
        self._task_counter: int = 0
    
    @property
    def current_state(self) -> Optional[ProgressState]:
        """Get current progress state."""
        return self._state
    
    @property
    def is_tracking(self) -> bool:
        """Check if currently tracking a task."""
        return self._state is not None and not self._state.is_complete
    
    def on_progress(self, callback: Callable[[ProgressEvent], None]) -> None:
        """
        Register a callback for progress events.
        
        Args:
            callback: Function called with ProgressEvent on each update
        """
        self._callbacks.append(callback)
    
    def _emit(self, event: ProgressEvent) -> None:
        """Emit a progress event to all callbacks."""
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:
                pass  # Don't fail on callback errors
    
    def _generate_task_id(self) -> str:
        """Generate a unique task ID."""
        self._task_counter += 1
        return f"task_{self._task_counter:04d}"
    
    def start_task(
        self,
        title: str,
        total_steps: int = 0,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProgressState:
        """
        Start tracking a new task.
        
        Args:
            title: Task title/description
            total_steps: Expected number of steps (0 = unknown)
            task_id: Optional task ID (auto-generated if not provided)
            metadata: Optional metadata
            
        Returns:
            ProgressState for the task
        """
        self._state = ProgressState(
            task_id=task_id or self._generate_task_id(),
            task_title=title,
            total_steps=total_steps,
        )
        self._step_start_time = time.time()
        
        self._emit(ProgressEvent(
            type=ProgressEventType.TASK_START,
            task_id=self._state.task_id,
            message=f"Starting: {title}",
            progress_percent=0,
            current_step=0,
            total_steps=total_steps,
            elapsed_seconds=0,
            metadata=metadata or {},
        ))
        
        return self._state
    
    def update(
        self,
        step: int,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProgressState:
        """
        Update progress to a specific step.
        
        Args:
            step: Current step number (1-indexed)
            message: Progress message
            metadata: Optional metadata
            
        Returns:
            Updated ProgressState
        """
        if not self._state:
            raise ValueError("No task in progress. Call start_task first.")
        
        # Record step time
        if self._step_start_time:
            step_time = time.time() - self._step_start_time
            self._state.step_times.append(step_time)
            self._step_start_time = time.time()
        
        self._state.current_step = step
        self._state.current_message = message
        
        # Calculate progress percentage
        if self._state.total_steps > 0:
            self._state.progress_percent = int((step / self._state.total_steps) * 100)
        
        self._emit(ProgressEvent(
            type=ProgressEventType.STEP_UPDATE,
            task_id=self._state.task_id,
            message=message,
            progress_percent=self._state.progress_percent,
            current_step=step,
            total_steps=self._state.total_steps,
            elapsed_seconds=self._state.elapsed_seconds,
            estimated_remaining_seconds=self._state.estimated_remaining_seconds,
            metadata=metadata or {},
        ))
        
        return self._state
    
    def increment(self, message: str = "", metadata: Optional[Dict[str, Any]] = None) -> ProgressState:
        """
        Increment progress by one step.
        
        Args:
            message: Progress message
            metadata: Optional metadata
            
        Returns:
            Updated ProgressState
        """
        if not self._state:
            raise ValueError("No task in progress. Call start_task first.")
        
        return self.update(
            self._state.current_step + 1,
            message or f"Step {self._state.current_step + 1}",
            metadata,
        )
    
    def set_total_steps(self, total: int) -> None:
        """Update the total number of steps (for dynamic progress)."""
        if self._state:
            self._state.total_steps = total
            # Recalculate percentage
            if total > 0:
                self._state.progress_percent = int((self._state.current_step / total) * 100)
    
    def complete(
        self,
        message: str = "Task completed",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProgressState:
        """
        Mark the current task as complete.
        
        Args:
            message: Completion message
            metadata: Optional metadata
            
        Returns:
            Final ProgressState
        """
        if not self._state:
            raise ValueError("No task in progress.")
        
        self._state.is_complete = True
        self._state.progress_percent = 100
        self._state.current_message = message
        
        self._emit(ProgressEvent(
            type=ProgressEventType.TASK_COMPLETE,
            task_id=self._state.task_id,
            message=message,
            progress_percent=100,
            current_step=self._state.current_step,
            total_steps=self._state.total_steps,
            elapsed_seconds=self._state.elapsed_seconds,
            metadata=metadata or {},
        ))
        
        return self._state
    
    def fail(
        self,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProgressState:
        """
        Mark the current task as failed.
        
        Args:
            error: Error message
            metadata: Optional metadata
            
        Returns:
            Final ProgressState
        """
        if not self._state:
            raise ValueError("No task in progress.")
        
        self._state.is_failed = True
        self._state.error_message = error
        self._state.current_message = f"Failed: {error}"
        
        self._emit(ProgressEvent(
            type=ProgressEventType.TASK_FAILED,
            task_id=self._state.task_id,
            message=f"Failed: {error}",
            progress_percent=self._state.progress_percent,
            current_step=self._state.current_step,
            total_steps=self._state.total_steps,
            elapsed_seconds=self._state.elapsed_seconds,
            metadata={"error": error, **(metadata or {})},
        ))
        
        return self._state
    
    def reset(self) -> None:
        """Reset progress state (cancel current tracking)."""
        self._state = None
        self._step_start_time = None
    
    def get_progress_bar(self, width: int = 30) -> str:
        """
        Generate a text progress bar.
        
        Args:
            width: Bar width in characters
            
        Returns:
            Text progress bar string
        """
        if not self._state:
            return "[" + "-" * width + "] 0%"
        
        filled = int((self._state.progress_percent / 100) * width)
        bar = "█" * filled + "░" * (width - filled)
        
        return f"[{bar}] {self._state.progress_percent}%"
    
    def get_summary(self) -> Dict[str, Any]:
        """Get progress summary for telemetry."""
        if not self._state:
            return {"tracking": False}
        
        return {
            "tracking": True,
            "task_id": self._state.task_id,
            "task_title": self._state.task_title,
            "progress_percent": self._state.progress_percent,
            "current_step": self._state.current_step,
            "total_steps": self._state.total_steps,
            "elapsed_seconds": self._state.elapsed_seconds,
            "estimated_remaining_seconds": self._state.estimated_remaining_seconds,
            "is_complete": self._state.is_complete,
            "is_failed": self._state.is_failed,
        }
