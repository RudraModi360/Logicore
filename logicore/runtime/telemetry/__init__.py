"""
Telemetry Collector: Comprehensive observability for agent execution.

Metrics:
- Loop detection events (type, frequency, recovery actions)
- Turn lifecycle (start, end, duration, status)
- Token usage (input, output, by category)
- Tool execution (calls, success rate, duration, retries)
- Context management (compressions, masking events)
- Recovery events (triggers, outcomes)
"""

from logicore.runtime.telemetry.collector import (
    TelemetryCollector,
    TelemetryEvent,
    TelemetryEventType,
    SessionMetrics,
    LoopStatistics,
)

__all__ = [
    "TelemetryCollector",
    "TelemetryEvent",
    "TelemetryEventType",
    "SessionMetrics",
    "LoopStatistics",
]
