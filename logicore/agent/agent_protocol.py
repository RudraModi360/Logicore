"""
AgentProtocol: Interface contract for ChatOrchestrator's dependency on Agent.

Defines the minimum set of attributes and methods that ChatOrchestrator
(and other components) require from an Agent instance. This enables:
- Mock-based testing without constructing a full Agent
- Compile-time safety via type checkers
- Clear documentation of the dependency boundary

Usage:
    from logicore.agent.agent_protocol import AgentProtocol

    def my_function(agent: AgentProtocol) -> None:
        # Type checkers will verify agent has the required attributes
        session = agent.get_session("default")
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from logicore.agent.session import AgentSession


@runtime_checkable
class AgentProtocol(Protocol):
    """Protocol defining what ChatOrchestrator needs from Agent.

    This is a structural typing contract — any object with these attributes
    and methods satisfies the protocol, regardless of its class hierarchy.
    """

    # --- Core attributes ---
    debug: bool
    model_name: str
    max_iterations: int
    role: str
    supports_tools: bool
    tools_disabled_reason: Optional[str]

    # --- Provider / Gateway ---
    provider: Any  # LLMProvider
    gateway: Any  # ProviderGateway

    # --- Context Engine ---
    context_engine: Any  # ContextEngine

    # --- Tool Management ---
    internal_tools: List[Any]
    disabled_tools: set
    workspace_root: Optional[str]
    tool_executor: Any  # ToolExecutor

    # --- Execution Tracking ---
    execution_log: List[str]

    # --- Callbacks ---
    callbacks: Dict[str, Any]

    # --- Telemetry ---
    telemetry_enabled: bool
    session_input_tokens: int
    session_output_tokens: int
    session_cache_read_tokens: int
    session_cache_write_tokens: int
    session_reasoning_tokens: int
    session_api_calls: int
    session_estimated_cost_usd: float

    # --- Task Management ---
    _task_manager: Any  # TaskManager (optional)

    # --- Loop Detection ---
    _loop_engine: Any  # LoopDetectionEngine (optional)

    # --- Reasoning ---
    _reasoning_controller: Any  # ReasoningController (optional)

    # --- Skills ---
    _skill_index_entries: Dict[str, Any]

    # --- Plan Mode ---
    _plan_mode_enabled: bool
    _planner: Any  # PlanService (optional)

    # --- Session Management ---
    def get_session(self, session_id: str) -> AgentSession: ...

    # --- Tool Result Serialization ---
    def _serialize_tool_result_for_model(
        self, tool_name: str, result: Dict[str, Any], reused: bool = False
    ) -> str: ...

    # --- Tool Path Normalization ---
    def _normalize_tool_paths(
        self, session: AgentSession, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]: ...

    def _update_tool_directory_context(
        self,
        session: AgentSession,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None: ...

    # --- Reminder Routing ---
    def _build_reminder_routing_hint(
        self, text: Any, tool_names: List[str]
    ) -> Optional[str]: ...

    def _is_reminder_like_request(self, text: Any) -> bool: ...

    def _has_unverified_reminder_claim(self, session: AgentSession) -> bool: ...

    # --- Walkthrough ---
    def _generate_execution_summary(self, session: AgentSession) -> str: ...

    def _generate_walkthrough_summary(
        self, session: AgentSession, text_for_reminder: Any
    ) -> Optional[str]: ...

    # --- Skill Tools ---
    def _register_skill_tools(self, skill: Any) -> None: ...

    def _rebuild_system_prompt_with_tools(self) -> None: ...

    # --- Tool Registration ---
    def register_tool_from_function(self, func: Any) -> None: ...
