"""
Hook System Types — Definitions for execution hooks.

Provides:
- HookPoint: Enumeration of available hook points
- HookResult: Result from hook execution
- HookContext: Context passed to hooks
- Hook: Base hook interface
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable, Union, Awaitable, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from logicore.providers.gateway import NormalizedMessage


class HookPoint(Enum):
    """
    Available hook points in the execution lifecycle.
    
    Execution order during a turn:
    1. BEFORE_MODEL - Before sending request to LLM
    2. AFTER_MODEL - After receiving LLM response (before tool execution)
    3. BEFORE_TOOL_SELECTION - Before deciding which tools to use
    4. AFTER_TOOL_SELECTION - After tools are selected (before execution)
    5. BEFORE_TOOL_EXECUTION - Before each tool is executed
    6. AFTER_TOOL_EXECUTION - After each tool completes
    7. BEFORE_CONTEXT_COMPRESSION - Before context is compressed
    8. AFTER_TURN - After a complete turn (including tool execution)
    """
    BEFORE_MODEL = auto()
    AFTER_MODEL = auto()
    BEFORE_TOOL_SELECTION = auto()
    AFTER_TOOL_SELECTION = auto()
    BEFORE_TOOL_EXECUTION = auto()
    AFTER_TOOL_EXECUTION = auto()
    BEFORE_CONTEXT_COMPRESSION = auto()
    AFTER_TURN = auto()


class HookAction(Enum):
    """Actions a hook can request."""
    CONTINUE = auto()       # Continue normal execution
    MODIFY = auto()         # Modify data and continue
    SKIP = auto()           # Skip the current operation
    SYNTHESIZE = auto()     # Synthesize a response (BeforeModel only)
    RETRY = auto()          # Retry the operation
    ABORT = auto()          # Abort execution


@dataclass
class HookContext:
    """
    Context passed to hooks containing relevant execution state.
    
    Attributes:
        hook_point: Which hook point is being executed
        messages: Current message history
        tools: Available tools
        model_response: Response from model (AFTER_MODEL only)
        tool_calls: Tool calls extracted from response
        tool_name: Current tool being executed (tool hooks only)
        tool_args: Arguments for current tool
        tool_result: Result from tool execution (AFTER_TOOL_EXECUTION only)
        metadata: Additional context-specific metadata
    """
    hook_point: HookPoint
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    model_response: Optional["NormalizedMessage"] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    session_id: Optional[str] = None
    turn_number: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class HookResult:
    """
    Result returned from a hook execution.
    
    Attributes:
        action: What action to take
        modified_messages: Modified messages (if action == MODIFY)
        modified_tools: Modified tools list (if action == MODIFY)
        synthesized_response: Synthesized response (if action == SYNTHESIZE)
        modified_tool_calls: Modified tool calls (if action == MODIFY)
        modified_tool_args: Modified tool arguments (if action == MODIFY)
        skip_reason: Reason for skipping (if action == SKIP)
        metadata: Additional result metadata
    """
    action: HookAction = HookAction.CONTINUE
    modified_messages: Optional[List[Dict[str, Any]]] = None
    modified_tools: Optional[List[Dict[str, Any]]] = None
    synthesized_response: Optional["NormalizedMessage"] = None
    modified_tool_calls: Optional[List[Dict[str, Any]]] = None
    modified_tool_args: Optional[Dict[str, Any]] = None
    skip_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Type alias for hook functions
# Hooks can be sync or async
SyncHookFn = Callable[[HookContext], HookResult]
AsyncHookFn = Callable[[HookContext], Awaitable[HookResult]]
HookFn = Union[SyncHookFn, AsyncHookFn]


@dataclass
class HookRegistration:
    """Registration info for a hook."""
    name: str
    hook_point: HookPoint
    hook_fn: HookFn
    priority: int = 100  # Lower = higher priority
    enabled: bool = True
    description: str = ""
    
    def __hash__(self):
        return hash((self.name, self.hook_point))
    
    def __eq__(self, other):
        if not isinstance(other, HookRegistration):
            return False
        return self.name == other.name and self.hook_point == other.hook_point


class HookError(Exception):
    """Exception raised when hook execution fails."""
    
    def __init__(self, hook_name: str, hook_point: HookPoint, original_error: Exception):
        self.hook_name = hook_name
        self.hook_point = hook_point
        self.original_error = original_error
        super().__init__(f"Hook '{hook_name}' at {hook_point.name} failed: {original_error}")
