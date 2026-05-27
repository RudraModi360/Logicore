"""
Loop Detection Engine: Multi-layer loop detection with pluggable detectors.

Architecture:
- LoopDetectionEngine: Orchestrates multiple detectors with weighted scoring
- Detectors: Pluggable strategies for different loop types
- Recovery: Composable strategies for graceful recovery

Detector Types:
- ConsecutiveToolCallDetector: Hash-based identical tool call detection
- ContentRepetitionDetector: Chunk-based streaming content analysis  
- SemanticLoopDetector: LLM-based conversation analysis
- StagnantStateDetector: No-progress detection
"""

from logicore.runtime.loop_detection.engine import (
    LoopDetectionEngine,
    LoopDetectionResult,
    LoopType,
    AgentEvent,
    AgentEventType,
)
from logicore.runtime.loop_detection.detectors import (
    LoopDetector,
    ConsecutiveToolCallDetector,
    ContentRepetitionDetector,
    StagnantStateDetector,
)
from logicore.runtime.loop_detection.recovery import (
    RecoveryStrategy,
    RecoveryAction,
    RecoveryActionType,
    RethinkStrategy,
    ToolCooldownStrategy,
    ContextResetStrategy,
    SummarizeProgressStrategy,
    get_recovery_action,
)

__all__ = [
    # Engine
    "LoopDetectionEngine",
    "LoopDetectionResult",
    "LoopType",
    "AgentEvent",
    "AgentEventType",
    # Detectors
    "LoopDetector",
    "ConsecutiveToolCallDetector",
    "ContentRepetitionDetector",
    "StagnantStateDetector",
    # Recovery
    "RecoveryStrategy",
    "RecoveryAction",
    "RecoveryActionType",
    "RethinkStrategy",
    "ToolCooldownStrategy",
    "ContextResetStrategy",
    "SummarizeProgressStrategy",
    "get_recovery_action",
]
