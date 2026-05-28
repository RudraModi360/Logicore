"""
Logicore Runtime: Production-Grade Agentic Execution Architecture

This package provides the core runtime components for bounded, observable,
and recoverable agent execution. Inspired by gemini-cli architecture patterns.

Components:
- RuntimeConfig: Centralized configuration (no hardcoded thresholds)
- TurnManager: Bounded execution with state machine
- LoopDetectionEngine: Multi-layer loop detection with recovery
- ContextWindowManager: Intelligent context compression and masking
- ToolScheduler: Tool execution with deduplication and retry
- TelemetryCollector: Comprehensive observability
- AgentRuntime: Orchestrator combining all components
- Reasoning: Configurable reasoning levels and thinking budgets
- Tracker: Hierarchical task tracking with dependencies
- Planner: Plan-before-execute workflow with approval gates
- Progress: Real-time progress tracking and visualization
"""

from logicore.runtime.config import (
    RuntimeConfig, 
    LoopDetectionConfig, 
    ContextConfig, 
    ToolConfig, 
    RetryConfig, 
    TelemetryConfig,
    ReasoningConfig,
    TrackerConfig,
    PlannerConfig,
)
from logicore.runtime.turn_manager import TurnManager, TurnContext, TurnStatus
from logicore.runtime.loop_detection import (
    LoopDetectionEngine,
    LoopDetectionResult,
    LoopType,
    AgentEvent,
    AgentEventType,
    RecoveryAction,
    RecoveryActionType,
)
from logicore.runtime.context import ContextWindowManager, ContextManagementResult
from logicore.runtime.scheduler import ToolScheduler, ToolCallRequest, ToolCallResult, ToolCallStatus
from logicore.runtime.telemetry import TelemetryCollector, TelemetryEvent, TelemetryEventType
from logicore.runtime.agent_runtime import AgentRuntime

# New modules for complex reasoning, task tracking, planning, and progress
from logicore.runtime.reasoning import (
    ReasoningLevel,
    ReasoningConfig as ReasoningConfigFull,
    ReasoningController,
    REASONING_PRESETS,
    get_reasoning_system_prompt_addon,
)
from logicore.runtime.tracker import (
    TrackerService,
    TrackerTask,
    TaskType,
    TaskStatus,
)
from logicore.runtime.planner import (
    PlanService,
    Plan,
    PlanStep,
    PlanStatus,
    StepStatus,
)
from logicore.runtime.progress import (
    ProgressService,
    ProgressState,
    ProgressEvent,
    ProgressEventType,
)

__all__ = [
    # Config
    "RuntimeConfig",
    "LoopDetectionConfig", 
    "ContextConfig",
    "ToolConfig",
    "RetryConfig",
    "TelemetryConfig",
    "ReasoningConfig",
    "TrackerConfig",
    "PlannerConfig",
    # Turn Management
    "TurnManager",
    "TurnContext",
    "TurnStatus",
    # Loop Detection
    "LoopDetectionEngine",
    "LoopDetectionResult",
    "LoopType",
    "AgentEvent",
    "AgentEventType",
    "RecoveryAction",
    "RecoveryActionType",
    # Context
    "ContextWindowManager",
    "ContextManagementResult",
    # Scheduler
    "ToolScheduler",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolCallStatus",
    # Telemetry
    "TelemetryCollector",
    "TelemetryEvent",
    "TelemetryEventType",
    # Runtime
    "AgentRuntime",
    # Reasoning
    "ReasoningLevel",
    "ReasoningConfigFull",
    "ReasoningController",
    "REASONING_PRESETS",
    "get_reasoning_system_prompt_addon",
    # Tracker
    "TrackerService",
    "TrackerTask",
    "TaskType",
    "TaskStatus",
    # Planner
    "PlanService",
    "Plan",
    "PlanStep",
    "PlanStatus",
    "StepStatus",
    # Progress
    "ProgressService",
    "ProgressState",
    "ProgressEvent",
    "ProgressEventType",
]
