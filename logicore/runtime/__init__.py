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
"""

from logicore.runtime.config import RuntimeConfig, LoopDetectionConfig, ContextConfig, ToolConfig, RetryConfig, TelemetryConfig
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

__all__ = [
    # Config
    "RuntimeConfig",
    "LoopDetectionConfig", 
    "ContextConfig",
    "ToolConfig",
    "RetryConfig",
    "TelemetryConfig",
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
]
