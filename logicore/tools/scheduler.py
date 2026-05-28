"""
Tool Scheduler Integration

This module provides tool scheduling capabilities within the tools/ domain.
It bridges to the runtime implementation while maintaining architectural alignment.

Features:
- Tool call deduplication (avoid repeated identical calls)
- Exponential backoff retry
- Per-tool cooldowns
- Execution state tracking

Usage:
    from logicore.tools.scheduler import ToolScheduler, schedule_tool

Once runtime/ is deprecated, this becomes the authoritative module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable, Awaitable

# Re-export from runtime for compatibility
try:
    from logicore.runtime.scheduler import (
        ToolScheduler as RuntimeToolScheduler,
        ToolCallRequest,
        ToolCallResult,
        ToolCallStatus,
        ToolCallState,
    )
    _RUNTIME_AVAILABLE = True
except ImportError:
    _RUNTIME_AVAILABLE = False


# === Standalone implementation ===

class ExecutionStatus(Enum):
    """Status of a tool execution."""
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"  # Deduplicated
    COOLDOWN = "cooldown"
    TIMEOUT = "timeout"


@dataclass
class ToolExecution:
    """Record of a tool execution."""
    call_id: str
    tool_name: str
    args: Dict[str, Any]
    status: ExecutionStatus
    
    # Result
    result: Optional[Any] = None
    error: Optional[str] = None
    
    # Timing
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    
    # Deduplication
    signature: str = ""
    reused_from: Optional[str] = None
    
    @property
    def duration_ms(self) -> Optional[float]:
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() * 1000
        return None
    
    @property
    def success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS


@dataclass
class SchedulerConfig:
    """Configuration for tool scheduler."""
    # Deduplication
    enable_deduplication: bool = True
    cache_ttl_seconds: int = 300
    
    # Retry
    max_retries: int = 3
    base_delay_ms: int = 500
    use_exponential_backoff: bool = True
    max_delay_ms: int = 30000
    jitter_factor: float = 0.1
    
    # Cooldowns
    default_cooldown_seconds: int = 60
    
    # Timeouts
    execution_timeout_seconds: int = 60
    
    # Concurrency
    max_concurrent: int = 10


class ToolScheduler:
    """
    Manages tool execution with deduplication, retry, and cooldowns.
    
    This is the preferred interface for tool scheduling within tools/
    architecture. It can delegate to runtime.ToolScheduler or operate
    standalone.
    
    Usage:
        scheduler = ToolScheduler()
        
        # Execute a tool
        result = await scheduler.execute(
            tool_name="read_file",
            args={"path": "test.py"},
            executor=my_executor_fn,
        )
        
        if result.success:
            print(result.result)
    """
    
    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
        use_runtime_scheduler: bool = False,
    ):
        self.config = config or SchedulerConfig()
        
        # Deduplication cache: signature -> (call_id, result, timestamp)
        self._cache: Dict[str, tuple[str, Any, float]] = {}
        
        # Cooldowns: tool_name -> cooldown_until timestamp
        self._cooldowns: Dict[str, float] = {}
        
        # Execution history
        self._history: List[ToolExecution] = []
        
        # Concurrency control
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        
        # Call counter for IDs
        self._call_counter = 0
        
        # Optional runtime scheduler
        self._runtime_scheduler = None
        if use_runtime_scheduler and _RUNTIME_AVAILABLE:
            from logicore.runtime import RuntimeConfig
            self._runtime_scheduler = RuntimeToolScheduler(RuntimeConfig())
    
    def _generate_call_id(self) -> str:
        """Generate unique call ID."""
        self._call_counter += 1
        return f"call_{self._call_counter}_{int(time.time() * 1000)}"
    
    def _get_signature(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Generate deduplication signature."""
        args_str = json.dumps(args, sort_keys=True)
        return hashlib.sha256(f"{tool_name}:{args_str}".encode()).hexdigest()
    
    def _get_cached(self, signature: str) -> Optional[tuple[str, Any]]:
        """Get cached result if within TTL."""
        if signature not in self._cache:
            return None
        
        call_id, result, timestamp = self._cache[signature]
        
        if time.time() - timestamp > self.config.cache_ttl_seconds:
            del self._cache[signature]
            return None
        
        return call_id, result
    
    def _cache_result(self, signature: str, call_id: str, result: Any) -> None:
        """Cache a successful result."""
        if not self.config.enable_deduplication:
            return
        
        self._cache[signature] = (call_id, result, time.time())
        
        # Limit cache size
        if len(self._cache) > 500:
            # Remove oldest
            oldest = sorted(self._cache.items(), key=lambda x: x[1][2])[:50]
            for key, _ in oldest:
                del self._cache[key]
    
    def is_cooled_down(self, tool_name: str) -> bool:
        """Check if a tool is in cooldown."""
        return time.time() < self._cooldowns.get(tool_name, 0)
    
    def apply_cooldown(self, tool_name: str, duration: Optional[int] = None) -> None:
        """Apply cooldown to a tool."""
        duration = duration or self.config.default_cooldown_seconds
        self._cooldowns[tool_name] = time.time() + duration
    
    def clear_cooldown(self, tool_name: str) -> None:
        """Clear cooldown for a tool."""
        self._cooldowns.pop(tool_name, None)
    
    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        executor: Callable[[str, Dict[str, Any]], Awaitable[Any]],
        allow_retry: bool = True,
        timeout: Optional[int] = None,
    ) -> ToolExecution:
        """
        Execute a tool with scheduling features.
        
        Args:
            tool_name: Name of the tool
            args: Tool arguments
            executor: Async function (name, args) -> result
            allow_retry: Enable retry on failure
            timeout: Execution timeout in seconds
        
        Returns:
            ToolExecution record
        """
        call_id = self._generate_call_id()
        signature = self._get_signature(tool_name, args)
        timeout = timeout or self.config.execution_timeout_seconds
        
        # Check cooldown
        if self.is_cooled_down(tool_name):
            exec_record = ToolExecution(
                call_id=call_id,
                tool_name=tool_name,
                args=args,
                status=ExecutionStatus.COOLDOWN,
                signature=signature,
                error=f"Tool '{tool_name}' is in cooldown",
            )
            self._history.append(exec_record)
            return exec_record
        
        # Check deduplication
        if self.config.enable_deduplication:
            cached = self._get_cached(signature)
            if cached:
                original_id, cached_result = cached
                exec_record = ToolExecution(
                    call_id=call_id,
                    tool_name=tool_name,
                    args=args,
                    status=ExecutionStatus.SKIPPED,
                    result=cached_result,
                    signature=signature,
                    reused_from=original_id,
                )
                self._history.append(exec_record)
                return exec_record
        
        # Execute with retry
        max_attempts = self.config.max_retries if allow_retry else 1
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                async with self._semaphore:
                    started_at = datetime.now()
                    
                    try:
                        result = await asyncio.wait_for(
                            executor(tool_name, args),
                            timeout=timeout,
                        )
                        
                        exec_record = ToolExecution(
                            call_id=call_id,
                            tool_name=tool_name,
                            args=args,
                            status=ExecutionStatus.SUCCESS,
                            result=result,
                            signature=signature,
                            started_at=started_at,
                            ended_at=datetime.now(),
                        )
                        
                        # Cache successful result
                        self._cache_result(signature, call_id, result)
                        
                        self._history.append(exec_record)
                        return exec_record
                        
                    except asyncio.TimeoutError:
                        exec_record = ToolExecution(
                            call_id=call_id,
                            tool_name=tool_name,
                            args=args,
                            status=ExecutionStatus.TIMEOUT,
                            signature=signature,
                            error=f"Timeout after {timeout}s",
                            started_at=started_at,
                            ended_at=datetime.now(),
                        )
                        self._history.append(exec_record)
                        return exec_record
                        
            except Exception as e:
                last_error = str(e)
                
                if attempt < max_attempts - 1:
                    # Calculate delay
                    if self.config.use_exponential_backoff:
                        delay = min(
                            self.config.base_delay_ms * (2 ** attempt),
                            self.config.max_delay_ms,
                        ) / 1000
                    else:
                        delay = self.config.base_delay_ms / 1000
                    
                    # Add jitter
                    import random
                    jitter = delay * self.config.jitter_factor * random.random()
                    
                    await asyncio.sleep(delay + jitter)
        
        # All retries exhausted
        exec_record = ToolExecution(
            call_id=call_id,
            tool_name=tool_name,
            args=args,
            status=ExecutionStatus.ERROR,
            signature=signature,
            error=last_error,
        )
        self._history.append(exec_record)
        return exec_record
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get execution statistics."""
        if not self._history:
            return {"total": 0}
        
        successful = sum(1 for e in self._history if e.success)
        skipped = sum(1 for e in self._history if e.status == ExecutionStatus.SKIPPED)
        durations = [e.duration_ms for e in self._history if e.duration_ms]
        
        return {
            "total": len(self._history),
            "successful": successful,
            "failed": len(self._history) - successful - skipped,
            "skipped_dedup": skipped,
            "success_rate": successful / len(self._history) if self._history else 0,
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
        }
    
    def clear(self) -> None:
        """Clear all state."""
        self._cache.clear()
        self._cooldowns.clear()
        self._history.clear()


# === Convenience function ===

async def schedule_tool(
    tool_name: str,
    args: Dict[str, Any],
    executor: Callable[[str, Dict[str, Any]], Awaitable[Any]],
    enable_dedup: bool = True,
    max_retries: int = 3,
) -> ToolExecution:
    """
    Quick tool scheduling with default settings.
    
    Usage:
        result = await schedule_tool(
            "read_file",
            {"path": "test.py"},
            my_executor,
        )
    """
    config = SchedulerConfig(
        enable_deduplication=enable_dedup,
        max_retries=max_retries,
    )
    scheduler = ToolScheduler(config)
    return await scheduler.execute(tool_name, args, executor)


# === Exports ===

__all__ = [
    # Core classes
    "ToolScheduler",
    "ToolExecution",
    "ExecutionStatus",
    "SchedulerConfig",
    # Convenience functions
    "schedule_tool",
]

# Add runtime exports if available
if _RUNTIME_AVAILABLE:
    __all__.extend([
        "RuntimeToolScheduler",
        "ToolCallRequest",
        "ToolCallResult",
        "ToolCallStatus",
        "ToolCallState",
    ])
