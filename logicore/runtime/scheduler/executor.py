"""
ToolScheduler: State machine-based tool execution with deduplication and retry.

Features:
- State machine: Scheduled → Validating → Executing → Success/Error/Cancelled
- Execution deduplication via content hash
- Exponential backoff retry
- Per-tool cooldowns
- Timeout enforcement
- Structured execution logs

Inspired by gemini-cli's Scheduler architecture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable, Awaitable, Set

from logicore.runtime.config import RuntimeConfig


class ToolCallStatus(Enum):
    """Status of a tool call in the execution lifecycle."""
    SCHEDULED = "scheduled"       # Queued for execution
    VALIDATING = "validating"     # Validating arguments
    EXECUTING = "executing"       # Currently running
    SUCCESS = "success"           # Completed successfully
    ERROR = "error"               # Failed with error
    CANCELLED = "cancelled"       # Cancelled before completion
    TIMEOUT = "timeout"           # Timed out
    DEDUPLICATED = "deduplicated" # Skipped as duplicate


@dataclass
class ToolCallRequest:
    """Request to execute a tool."""
    call_id: str
    name: str
    args: Dict[str, Any]
    
    # Metadata
    session_id: str = "default"
    turn_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    
    # Execution hints
    timeout_seconds: Optional[int] = None
    allow_retry: bool = True
    
    def get_signature(self) -> str:
        """Get unique signature for deduplication."""
        args_str = json.dumps(self.args, sort_keys=True)
        return hashlib.sha256(f"{self.name}:{args_str}".encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "call_id": self.call_id,
            "name": self.name,
            "args": self.args,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ToolCallResult:
    """Result of a tool execution."""
    call_id: str
    name: str
    status: ToolCallStatus
    
    # Result data
    result: Optional[Any] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    # Timing
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    
    # Retry info
    attempts: int = 1
    
    # Deduplication
    reused_from: Optional[str] = None  # call_id of original if deduplicated
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Get execution duration in milliseconds."""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() * 1000
        return None
    
    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.status == ToolCallStatus.SUCCESS
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "call_id": self.call_id,
            "name": self.name,
            "status": self.status.value,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "reused_from": self.reused_from,
        }


@dataclass
class ToolCallState:
    """Current state of a tool call."""
    request: ToolCallRequest
    status: ToolCallStatus
    result: Optional[ToolCallResult] = None
    
    # Progress tracking
    progress_message: Optional[str] = None
    progress_percent: Optional[float] = None
    
    # State timestamps
    state_history: List[tuple[ToolCallStatus, datetime]] = field(default_factory=list)
    
    def transition(self, new_status: ToolCallStatus) -> None:
        """Transition to new status."""
        self.state_history.append((self.status, datetime.now()))
        self.status = new_status


# Type for tool executor function
ToolExecutorFn = Callable[[str, Dict[str, Any]], Awaitable[Any]]


class ToolScheduler:
    """
    Coordinates tool execution with state tracking and retry logic.
    
    Features:
    - State machine for each tool call
    - Execution deduplication
    - Exponential backoff retry
    - Cooldown management
    - Concurrent execution support
    
    Usage:
        scheduler = ToolScheduler(config, executor)
        
        # Schedule tool calls
        results = await scheduler.schedule([
            ToolCallRequest(call_id="1", name="read_file", args={"path": "test.py"}),
            ToolCallRequest(call_id="2", name="write_file", args={"path": "out.py", "content": "..."}),
        ])
        
        for result in results:
            if result.success:
                print(f"Tool {result.name} succeeded")
            else:
                print(f"Tool {result.name} failed: {result.error}")
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        executor: Optional[ToolExecutorFn] = None,
    ):
        """
        Args:
            config: Runtime configuration
            executor: Function to execute tools (name, args) -> result
        """
        self.config = config
        self._executor = executor
        
        # Active tool calls by ID
        self._active: Dict[str, ToolCallState] = {}
        
        # Deduplication cache: signature -> (call_id, result, timestamp)
        self._dedup_cache: Dict[str, tuple[str, Any, float]] = {}
        
        # Cooldowns: tool_name -> cooldown_until timestamp
        self._cooldowns: Dict[str, float] = {}
        
        # Execution history for session
        self._history: Dict[str, List[ToolCallResult]] = {}  # session_id -> results
        
        # Concurrency control
        self._semaphore = asyncio.Semaphore(10)  # Max concurrent executions
    
    def set_executor(self, executor: ToolExecutorFn) -> None:
        """Set the tool executor function."""
        self._executor = executor
    
    def _get_cached_result(self, signature: str) -> Optional[tuple[str, Any]]:
        """Get cached result for signature if within TTL."""
        if signature not in self._dedup_cache:
            return None
        
        call_id, result, timestamp = self._dedup_cache[signature]
        ttl = self.config.tool.cache_ttl_seconds
        
        if time.time() - timestamp > ttl:
            # Expired
            del self._dedup_cache[signature]
            return None
        
        return call_id, result
    
    def _cache_result(self, signature: str, call_id: str, result: Any) -> None:
        """Cache a successful result for deduplication."""
        if not self.config.tool.enable_deduplication:
            return
        
        self._dedup_cache[signature] = (call_id, result, time.time())
        
        # Limit cache size
        if len(self._dedup_cache) > 1000:
            # Remove oldest entries
            sorted_entries = sorted(
                self._dedup_cache.items(),
                key=lambda x: x[1][2],  # timestamp
            )
            for key, _ in sorted_entries[:100]:
                del self._dedup_cache[key]
    
    def is_tool_cooled_down(self, tool_name: str) -> bool:
        """Check if a tool is in cooldown."""
        cooldown_until = self._cooldowns.get(tool_name, 0)
        return time.time() < cooldown_until
    
    def apply_cooldown(self, tool_name: str, duration_seconds: Optional[int] = None) -> None:
        """Apply cooldown to a tool."""
        duration = duration_seconds or self.config.tool.default_cooldown_seconds
        self._cooldowns[tool_name] = time.time() + duration
    
    def clear_cooldown(self, tool_name: str) -> None:
        """Clear cooldown for a tool."""
        self._cooldowns.pop(tool_name, None)
    
    def get_cooled_down_tools(self) -> List[str]:
        """Get list of tools currently in cooldown."""
        now = time.time()
        return [name for name, until in self._cooldowns.items() if until > now]
    
    async def schedule(
        self,
        requests: List[ToolCallRequest],
    ) -> List[ToolCallResult]:
        """
        Schedule and execute tool calls.
        
        Args:
            requests: List of tool call requests
        
        Returns:
            List of results in same order as requests
        """
        if not requests:
            return []
        
        if not self._executor:
            return [
                ToolCallResult(
                    call_id=req.call_id,
                    name=req.name,
                    status=ToolCallStatus.ERROR,
                    error="No tool executor configured",
                    error_type="ConfigurationError",
                )
                for req in requests
            ]
        
        # Process each request
        results = []
        for request in requests:
            result = await self._execute_single(request)
            results.append(result)
            
            # Store in history
            session_id = request.session_id
            if session_id not in self._history:
                self._history[session_id] = []
            self._history[session_id].append(result)
        
        return results
    
    async def _execute_single(self, request: ToolCallRequest) -> ToolCallResult:
        """Execute a single tool call with retry logic."""
        # Check cooldown
        if self.is_tool_cooled_down(request.name):
            return ToolCallResult(
                call_id=request.call_id,
                name=request.name,
                status=ToolCallStatus.ERROR,
                error=f"Tool '{request.name}' is in cooldown",
                error_type="CooldownError",
            )
        
        # Check deduplication
        signature = request.get_signature()
        cached = self._get_cached_result(signature)
        
        if cached and self.config.tool.enable_deduplication:
            original_id, cached_result = cached
            return ToolCallResult(
                call_id=request.call_id,
                name=request.name,
                status=ToolCallStatus.DEDUPLICATED,
                result=cached_result,
                reused_from=original_id,
            )
        
        # Create state
        state = ToolCallState(request=request, status=ToolCallStatus.SCHEDULED)
        self._active[request.call_id] = state
        
        try:
            # Transition to executing
            state.transition(ToolCallStatus.EXECUTING)
            
            # Execute with retry
            result = await self._execute_with_retry(request, state)
            
            # Cache successful results
            if result.success:
                self._cache_result(signature, request.call_id, result.result)
            
            return result
            
        finally:
            # Clean up active state
            self._active.pop(request.call_id, None)
    
    async def _execute_with_retry(
        self,
        request: ToolCallRequest,
        state: ToolCallState,
    ) -> ToolCallResult:
        """Execute with exponential backoff retry."""
        max_attempts = self.config.retry.max_attempts if request.allow_retry else 1
        base_delay = self.config.retry.base_delay_ms / 1000  # Convert to seconds
        use_exponential = self.config.retry.use_exponential_backoff
        max_delay = self.config.retry.max_delay_ms / 1000
        
        last_error = None
        last_error_type = None
        
        for attempt in range(max_attempts):
            try:
                async with self._semaphore:
                    started_at = datetime.now()
                    
                    # Apply timeout
                    timeout = request.timeout_seconds or self.config.tool.execution_timeout_seconds
                    
                    try:
                        result = await asyncio.wait_for(
                            self._executor(request.name, request.args),
                            timeout=timeout,
                        )
                        
                        state.transition(ToolCallStatus.SUCCESS)
                        
                        return ToolCallResult(
                            call_id=request.call_id,
                            name=request.name,
                            status=ToolCallStatus.SUCCESS,
                            result=result,
                            started_at=started_at,
                            ended_at=datetime.now(),
                            attempts=attempt + 1,
                        )
                        
                    except asyncio.TimeoutError:
                        state.transition(ToolCallStatus.TIMEOUT)
                        return ToolCallResult(
                            call_id=request.call_id,
                            name=request.name,
                            status=ToolCallStatus.TIMEOUT,
                            error=f"Tool execution timed out after {timeout}s",
                            error_type="TimeoutError",
                            started_at=started_at,
                            ended_at=datetime.now(),
                            attempts=attempt + 1,
                        )
                        
            except asyncio.CancelledError:
                state.transition(ToolCallStatus.CANCELLED)
                return ToolCallResult(
                    call_id=request.call_id,
                    name=request.name,
                    status=ToolCallStatus.CANCELLED,
                    error="Execution cancelled",
                    attempts=attempt + 1,
                )
                
            except Exception as e:
                last_error = str(e)
                last_error_type = type(e).__name__
                
                # Check if retryable
                if not self._is_retryable(e) or attempt >= max_attempts - 1:
                    break
                
                # Calculate delay
                if use_exponential:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                else:
                    delay = base_delay
                
                # Add jitter
                import random
                jitter = delay * self.config.retry.jitter_factor * random.random()
                delay += jitter
                
                await asyncio.sleep(delay)
        
        # All retries exhausted
        state.transition(ToolCallStatus.ERROR)
        
        return ToolCallResult(
            call_id=request.call_id,
            name=request.name,
            status=ToolCallStatus.ERROR,
            error=last_error,
            error_type=last_error_type,
            attempts=max_attempts,
        )
    
    def _is_retryable(self, error: Exception) -> bool:
        """Check if error is retryable."""
        error_str = str(error).lower()
        
        for pattern in self.config.retry.retryable_patterns:
            if pattern.lower() in error_str:
                return True
        
        return False
    
    def get_tool_state(self, call_id: str) -> Optional[ToolCallState]:
        """Get current state of a tool call."""
        return self._active.get(call_id)
    
    def get_history(self, session_id: str) -> List[ToolCallResult]:
        """Get execution history for a session."""
        return self._history.get(session_id, [])
    
    def get_statistics(self, session_id: str) -> Dict[str, Any]:
        """Get execution statistics for a session."""
        history = self._history.get(session_id, [])
        
        if not history:
            return {
                "total_calls": 0,
                "success_rate": 0.0,
                "average_duration_ms": 0.0,
            }
        
        successful = sum(1 for r in history if r.success)
        durations = [r.duration_ms for r in history if r.duration_ms]
        
        by_tool: Dict[str, Dict[str, int]] = {}
        for result in history:
            if result.name not in by_tool:
                by_tool[result.name] = {"success": 0, "error": 0, "total": 0}
            by_tool[result.name]["total"] += 1
            if result.success:
                by_tool[result.name]["success"] += 1
            else:
                by_tool[result.name]["error"] += 1
        
        return {
            "total_calls": len(history),
            "successful": successful,
            "failed": len(history) - successful,
            "success_rate": successful / len(history) if history else 0.0,
            "average_duration_ms": sum(durations) / len(durations) if durations else 0.0,
            "deduplicated": sum(1 for r in history if r.status == ToolCallStatus.DEDUPLICATED),
            "by_tool": by_tool,
        }
    
    def clear_session(self, session_id: str) -> None:
        """Clear history for a session."""
        self._history.pop(session_id, None)
    
    def cancel(self, call_id: str) -> bool:
        """Cancel a scheduled/executing tool call."""
        state = self._active.get(call_id)
        if not state:
            return False
        
        if state.status in (ToolCallStatus.SCHEDULED, ToolCallStatus.EXECUTING):
            state.transition(ToolCallStatus.CANCELLED)
            return True
        
        return False
