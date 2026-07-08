"""
AgentRuntime: Main orchestrator combining all runtime components.

Integrates:
- TurnManager: Bounded execution
- LoopDetectionEngine: Multi-layer loop detection
- ContextWindowManager: Context compression and masking
- ToolScheduler: Tool execution with retry
- TelemetryCollector: Observability

This replaces the monolithic execution loop in agent.py with a modular,
testable, and extensible architecture.

Usage:
    # Create runtime
    runtime = AgentRuntime.create(llm_provider, model_name="gpt-4")
    
    # Or with custom config
    config = RuntimeConfig(max_turns=60)
    runtime = AgentRuntime(config, llm_provider, model_name="gpt-4")
    
    # Execute a turn
    async with runtime.turn(session_id) as ctx:
        # Process tool calls through scheduler
        results = await runtime.execute_tools(tool_calls, session_id)
        
        # Check for loops
        loop_result = await runtime.check_loop(event, session_id)
        if loop_result.detected:
            recovery = runtime.get_recovery_action(loop_result)
            # Apply recovery...
        
        # Manage context before LLM call
        managed_messages = await runtime.manage_context(messages, session_id)
"""

from __future__ import annotations

import uuid
from typing import Optional, Dict, List, Any, Callable, Awaitable

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.turn_manager import TurnManager, TurnContext, TurnStatus
from logicore.runtime.loop_detection import (
    LoopDetectionEngine,
    LoopDetectionResult,
    AgentEvent,
    AgentEventType,
    RecoveryAction,
    get_recovery_action,
)
from logicore.runtime.context import ContextWindowManager, ContextManagementResult
from logicore.runtime.scheduler import (
    ToolScheduler,
    ToolCallRequest,
    ToolCallResult,
)
from logicore.runtime.telemetry import (
    TelemetryCollector,
    TelemetryEvent,
    TelemetryEventType,
)


# Type for LLM provider
LLMProvider = Any


class AgentRuntime:
    """
    Production-grade orchestrator for agent execution.
    
    Combines all runtime components into a unified interface:
    - Bounded turn execution
    - Multi-layer loop detection with recovery
    - Intelligent context management
    - Robust tool execution
    - Comprehensive telemetry
    
    This is designed to be used by Agent.chat() to delegate
    the complex orchestration logic while maintaining backward
    compatibility with the existing API.
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        provider: Optional[LLMProvider] = None,
        model_name: str = "default",
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        """
        Args:
            config: Runtime configuration
            provider: LLM provider for chat and compression
            model_name: Model name for context window lookup
            tool_executor: Function to execute tools
        """
        self.config = config
        self.llm = provider
        self.model_name = model_name
        
        # Initialize components
        self.turn_manager = TurnManager(config)
        self.loop_engine = LoopDetectionEngine(config)
        self.context_manager = ContextWindowManager(
            config,
            provider,
            model_name,
        )
        self.tool_scheduler = ToolScheduler(config, tool_executor)
        self.telemetry = TelemetryCollector(config)
        
        # Wire up telemetry hooks
        self._setup_telemetry_hooks()
    
    @classmethod
    def create(
        cls,
        provider: Optional[LLMProvider] = None,
        model_name: str = "default",
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
        **config_overrides: Any,
    ) -> "AgentRuntime":
        """
        Factory method to create runtime with optional config overrides.
        
        Args:
            provider: LLM provider
            model_name: Model name
            tool_executor: Tool execution function
            **config_overrides: Override RuntimeConfig fields
        
        Returns:
            Configured AgentRuntime instance
        """
        # Load base config from settings
        try:
            config = RuntimeConfig.from_settings()
        except Exception:
            config = RuntimeConfig()
        
        # Apply overrides
        for key, value in config_overrides.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        return cls(config, provider, model_name, tool_executor)
    
    def _setup_telemetry_hooks(self) -> None:
        """Wire up telemetry collection from all components."""
        # Turn lifecycle hooks
        async def on_turn_start(turn: TurnContext) -> None:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.TURN_START,
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                data={"turn_number": turn.turn_number},
            ))
        
        async def on_turn_end(turn: TurnContext) -> None:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.TURN_END,
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                duration_ms=turn.duration_ms,
                data={
                    "success": turn.status == TurnStatus.COMPLETED,
                    "status": turn.status.value,
                    "tool_calls": turn.tool_calls,
                },
            ))
        
        self.turn_manager.register_on_turn_start(on_turn_start)
        self.turn_manager.register_on_turn_end(on_turn_end)
        
        # Loop detection hooks
        async def on_loop_detected(result: LoopDetectionResult) -> None:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.LOOP_DETECTED,
                data={
                    "loop_type": result.loop_type.value if result.loop_type else None,
                    "confidence": result.confidence,
                    "detail": result.detail,
                    "repetition_count": result.repetition_count,
                },
            ))
        
        self.loop_engine.register_on_detection(on_loop_detected)
    
    def set_tool_executor(
        self,
        executor: Callable[[str, Dict[str, Any]], Awaitable[Any]],
    ) -> None:
        """Set the tool execution function."""
        self.tool_scheduler.set_executor(executor)
    
    def set_llm_provider(self, provider: LLMProvider, model_name: Optional[str] = None) -> None:
        """Set or update the LLM provider."""
        self.llm = provider
        if model_name:
            self.model_name = model_name
        
        # Update context manager
        self.context_manager = ContextWindowManager(
            self.config,
            provider,
            model_name or self.model_name,
        )
    
    # --- Turn Management ---
    
    async def start_turn(
        self,
        session_id: str,
        parent_turn_id: Optional[str] = None,
    ) -> TurnContext:
        """Start a new turn."""
        return await self.turn_manager.start_turn(session_id, parent_turn_id)
    
    async def end_turn(
        self,
        turn_id: str,
        status: TurnStatus = TurnStatus.COMPLETED,
        error: Optional[str] = None,
    ) -> TurnContext:
        """End a turn."""
        return await self.turn_manager.end_turn(turn_id, status, error)
    
    def turn(self, session_id: str, parent_turn_id: Optional[str] = None):
        """Context manager for turn execution."""
        return self.turn_manager.turn(session_id, parent_turn_id)
    
    def get_remaining_turns(self, session_id: str) -> int:
        """Get remaining turn budget."""
        return self.turn_manager.get_remaining_turns(session_id)
    
    def is_budget_exceeded(self, session_id: str) -> bool:
        """Check if turn budget is exceeded."""
        return self.turn_manager.is_budget_exceeded(session_id)
    
    # --- Loop Detection ---
    
    async def check_loop(
        self,
        event: AgentEvent,
        session_id: str = "default",
    ) -> LoopDetectionResult:
        """Check an event for loop conditions."""
        return await self.loop_engine.check(event, session_id)
    
    async def check_loop_with_llm(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        user_prompt: Optional[str] = None,
    ) -> LoopDetectionResult:
        """Perform LLM-based loop detection."""
        return await self.loop_engine.check_with_llm(
            messages,
            session_id,
            user_prompt,
            self.llm,
        )
    
    def get_recovery_action(
        self,
        result: LoopDetectionResult,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> RecoveryAction:
        """Get recovery action for a detected loop."""
        if not result.detected or not result.loop_type or not result.suggested_escalation:
            from logicore.runtime.loop_detection.recovery import RecoveryAction, RecoveryActionType
            return RecoveryAction(action_type=RecoveryActionType.NO_OP)
        
        return get_recovery_action(
            result.loop_type.value,
            result.detail,
            result.suggested_escalation,
            session_context,
        )
    
    def disable_loop_detection(self, session_id: str) -> None:
        """Disable loop detection for a session."""
        self.loop_engine.disable_for_session(session_id)
    
    def enable_loop_detection(self, session_id: str) -> None:
        """Re-enable loop detection for a session."""
        self.loop_engine.enable_for_session(session_id)
    
    def is_tool_cooled_down(self, session_id: str, tool_name: str) -> bool:
        """Check if a tool is in cooldown."""
        return self.loop_engine.is_tool_cooled_down(session_id, tool_name)
    
    def apply_tool_cooldown(
        self,
        session_id: str,
        tool_name: str,
        duration_seconds: Optional[int] = None,
    ) -> None:
        """Apply cooldown to a tool."""
        self.loop_engine.apply_tool_cooldown(session_id, tool_name, duration_seconds)
    
    # --- Context Management ---
    
    async def manage_context(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> tuple[ContextManagementResult, List[Dict[str, Any]]]:
        """Manage context window (compress, mask as needed)."""
        result, managed = await self.context_manager.manage(messages, session_id)
        
        # Record telemetry
        if result.compressed:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.CONTEXT_COMPRESSED,
                session_id=session_id,
                data={"tokens_saved": result.tokens_saved},
            ))
        
        if result.masked:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.CONTEXT_MASKED,
                session_id=session_id,
                data={"tokens_saved": result.masking_result.tokens_saved if result.masking_result else 0},
            ))
        
        return result, managed
    
    def get_budget_status(self) -> Dict[str, Any]:
        """Get current context budget status."""
        return self.context_manager.get_budget_status()
    
    # --- Tool Execution ---
    
    async def execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        session_id: str = "default",
        turn_id: Optional[str] = None,
    ) -> List[ToolCallResult]:
        """
        Execute tool calls through the scheduler.
        
        Args:
            tool_calls: List of tool calls from LLM response
            session_id: Session identifier
            turn_id: Optional turn identifier
        
        Returns:
            List of ToolCallResults
        """
        # Convert to ToolCallRequest objects
        requests = []
        for tc in tool_calls:
            # Handle both dict and object formats
            if isinstance(tc, dict):
                func = tc.get("function", tc)
                name = func.get("name", tc.get("name"))
                args = func.get("arguments", tc.get("args", {}))
                call_id = tc.get("id", str(uuid.uuid4()))
            else:
                func = getattr(tc, "function", tc)
                name = getattr(func, "name", None)
                args = getattr(func, "arguments", {})
                call_id = getattr(tc, "id", str(uuid.uuid4()))
            
            if not name:
                continue
            
            # Parse args if string
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            
            requests.append(ToolCallRequest(
                call_id=call_id,
                name=name,
                args=args,
                session_id=session_id,
                turn_id=turn_id,
            ))
        
        # Execute through scheduler
        results = await self.tool_scheduler.schedule(requests)
        
        # Record telemetry
        for result in results:
            self.telemetry.record_event(TelemetryEvent(
                type=TelemetryEventType.TOOL_CALL_END,
                session_id=session_id,
                turn_id=turn_id,
                duration_ms=result.duration_ms,
                data={
                    "tool_name": result.name,
                    "success": result.success,
                    "status": result.status.value,
                    "attempts": result.attempts,
                },
            ))
        
        return results
    
    def get_tool_statistics(self, session_id: str) -> Dict[str, Any]:
        """Get tool execution statistics."""
        return self.tool_scheduler.get_statistics(session_id)
    
    # --- Telemetry ---
    
    def get_session_metrics(self, session_id: str):
        """Get telemetry metrics for a session."""
        return self.telemetry.get_session_metrics(session_id)
    
    def get_loop_statistics(self):
        """Get loop detection statistics."""
        return self.telemetry.get_loop_statistics()
    
    def export_telemetry(self, session_id: Optional[str] = None) -> str:
        """Export telemetry as JSON."""
        return self.telemetry.export_json(session_id)
    
    # --- Session Management ---
    
    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        self.turn_manager.clear_session(session_id)
        self.loop_engine.reset_session(session_id)
        self.context_manager.clear_session(session_id)
        self.tool_scheduler.clear_session(session_id)
        self.telemetry.clear_session(session_id)
    
    def reset_session_budget(self, session_id: str) -> None:
        """Reset turn budget for a session."""
        self.turn_manager.reset_session_budget(session_id)
    
    # --- Utility ---
    
    def create_tool_call_event(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> AgentEvent:
        """Create a tool call event for loop detection."""
        return AgentEvent(
            type=AgentEventType.TOOL_CALL,
            tool_name=tool_name,
            tool_args=tool_args,
        )
    
    def create_content_event(self, content: str) -> AgentEvent:
        """Create a content event for loop detection."""
        return AgentEvent(
            type=AgentEventType.CONTENT,
            content=content,
        )
    
    def create_turn_event(
        self,
        turn_id: str,
        turn_number: int,
        event_type: AgentEventType = AgentEventType.TURN_START,
    ) -> AgentEvent:
        """Create a turn event for loop detection."""
        return AgentEvent(
            type=event_type,
            turn_id=turn_id,
            turn_number=turn_number,
        )
