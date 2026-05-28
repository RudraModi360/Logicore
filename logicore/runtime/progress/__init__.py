"""
Progress Service Module

Provides real-time progress tracking and visualization for agent execution.
Emits progress events for UI integration.

Components:
- ProgressState: Current progress state
- ProgressService: Progress tracking and event emission

Usage:
    from logicore.runtime.progress import ProgressService
    
    progress = ProgressService()
    progress.start_task("Implementing feature", total_steps=5)
    progress.update(1, "Analyzing requirements")
    progress.update(2, "Creating models")
    progress.complete("Feature implemented successfully")
"""

from logicore.runtime.progress.service import (
    ProgressState,
    ProgressEvent,
    ProgressEventType,
    ProgressService,
)

__all__ = [
    "ProgressState",
    "ProgressEvent",
    "ProgressEventType",
    "ProgressService",
]
