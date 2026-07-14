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
- Planner: Plan-before-execute workflow with approval gates
"""

from logicore.runtime.config import (
    RuntimeConfig, 
    LoopDetectionConfig, 
    ContextConfig, 
    ToolConfig, 
    RetryConfig, 
    TelemetryConfig,
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
from logicore.runtime.context import EngineResult  # Public alias for ContextManagementResult
from logicore.runtime.scheduler import ToolScheduler, ToolCallRequest, ToolCallResult, ToolCallStatus
from logicore.runtime.telemetry import TelemetryCollector, TelemetryEvent, TelemetryEventType
from logicore.runtime.agent_runtime import AgentRuntime

# New modules for complex reasoning and planning
from logicore.runtime.reasoning import (
    ReasoningLevel,
    ReasoningConfig,
    ReasoningController,
    REASONING_PRESETS,
    get_reasoning_system_prompt_addon,
)
from logicore.runtime.planner import (
    PlanService,
    Plan,
    PlanStep,
    PlanStatus,
    StepStatus,
)

# Hooks system for execution lifecycle customization
from logicore.runtime.hooks import (
    HookSystem,
    HookPoint,
    HookAction,
    HookContext,
    HookResult,
    HookRegistration,
    get_default_hook_system,
    set_default_hook_system,
)

# Thought parsing for reasoning analysis
from logicore.runtime.reasoning import (
    ThoughtParser,
    ThoughtAnalysis,
    ParsedThought,
    ThoughtType,
    parse_thoughts,
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
    "EngineResult",
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
    "ReasoningConfig",
    "ReasoningController",
    "REASONING_PRESETS",
    "get_reasoning_system_prompt_addon",
    # Thought Parsing
    "ThoughtParser",
    "ThoughtAnalysis",
    "ParsedThought",
    "ThoughtType",
    "parse_thoughts",
    # Hooks
    "HookSystem",
    "HookPoint",
    "HookAction",
    "HookContext",
    "HookResult",
    "HookRegistration",
    "get_default_hook_system",
    "set_default_hook_system",
    # Planner
    "PlanService",
    "Plan",
    "PlanStep",
    "PlanStatus",
    "StepStatus",
]
