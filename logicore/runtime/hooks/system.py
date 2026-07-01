"""
Hook System — Execution lifecycle hooks for agent customization.

Inspired by gemini-cli's hookSystem.ts, provides:
- Registration and management of hooks at various execution points
- Priority-based hook ordering
- Async/sync hook support
- Result aggregation from multiple hooks
- Error isolation and logging

Usage:
    hooks = HookSystem()
    
    # Register a hook
    @hooks.register(HookPoint.BEFORE_MODEL, priority=10)
    async def add_system_context(ctx: HookContext) -> HookResult:
        ctx.messages.insert(0, {"role": "system", "content": "Extra context"})
        return HookResult(action=HookAction.MODIFY, modified_messages=ctx.messages)
    
    # Execute hooks
    result = await hooks.execute(HookPoint.BEFORE_MODEL, context)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Callable, Any, TYPE_CHECKING

from .types import (
    HookPoint,
    HookAction,
    HookContext,
    HookResult,
    HookRegistration,
    HookFn,
    HookError,
)

if TYPE_CHECKING:
    from logicore.gateway.gateway import NormalizedMessage

logger = logging.getLogger(__name__)


class HookSystem:
    """
    Manages execution hooks for agent lifecycle customization.
    
    Features:
    - Register hooks for specific execution points
    - Priority-based execution order
    - Support for both sync and async hooks
    - Result aggregation with conflict resolution
    - Error isolation (failing hooks don't break execution)
    - Hook enable/disable control
    
    Example:
        hooks = HookSystem()
        
        # Register with decorator
        @hooks.register(HookPoint.AFTER_MODEL)
        def log_response(ctx: HookContext) -> HookResult:
            print(f"Model said: {ctx.model_response.content[:100]}")
            return HookResult()
        
        # Or register directly
        hooks.add_hook(
            name="reasoning_escalator",
            hook_point=HookPoint.AFTER_MODEL,
            hook_fn=my_escalation_hook,
            priority=5,
        )
        
        # Execute hooks
        result = await hooks.execute(HookPoint.AFTER_MODEL, context)
    """
    
    def __init__(self, fail_fast: bool = False):
        """
        Initialize hook system.
        
        Args:
            fail_fast: If True, stop on first hook error. If False, log and continue.
        """
        self._hooks: Dict[HookPoint, List[HookRegistration]] = defaultdict(list)
        self._fail_fast = fail_fast
        self._execution_stats: Dict[str, Dict[str, Any]] = {}
    
    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------
    
    def register(
        self,
        hook_point: HookPoint,
        priority: int = 100,
        name: Optional[str] = None,
        description: str = "",
    ) -> Callable[[HookFn], HookFn]:
        """
        Decorator for registering hooks.
        
        Args:
            hook_point: When to execute this hook
            priority: Execution order (lower = earlier)
            name: Hook name (defaults to function name)
            description: Human-readable description
        
        Example:
            @hooks.register(HookPoint.BEFORE_MODEL, priority=10)
            async def my_hook(ctx: HookContext) -> HookResult:
                return HookResult()
        """
        def decorator(fn: HookFn) -> HookFn:
            hook_name = name or fn.__name__
            self.add_hook(
                name=hook_name,
                hook_point=hook_point,
                hook_fn=fn,
                priority=priority,
                description=description,
            )
            return fn
        return decorator
    
    def add_hook(
        self,
        name: str,
        hook_point: HookPoint,
        hook_fn: HookFn,
        priority: int = 100,
        enabled: bool = True,
        description: str = "",
    ) -> None:
        """
        Register a hook programmatically.
        
        Args:
            name: Unique name for this hook
            hook_point: When to execute this hook
            hook_fn: The hook function (sync or async)
            priority: Execution order (lower = earlier)
            enabled: Whether hook is initially enabled
            description: Human-readable description
        """
        # Remove existing hook with same name at this point
        self._hooks[hook_point] = [
            h for h in self._hooks[hook_point] 
            if h.name != name
        ]
        
        registration = HookRegistration(
            name=name,
            hook_point=hook_point,
            hook_fn=hook_fn,
            priority=priority,
            enabled=enabled,
            description=description,
        )
        
        self._hooks[hook_point].append(registration)
        # Sort by priority
        self._hooks[hook_point].sort(key=lambda h: h.priority)
        
        logger.debug(f"Registered hook '{name}' at {hook_point.name} (priority={priority})")
    
    def remove_hook(self, name: str, hook_point: Optional[HookPoint] = None) -> bool:
        """
        Remove a hook by name.
        
        Args:
            name: Hook name to remove
            hook_point: Specific hook point (or None for all points)
        
        Returns:
            True if any hooks were removed
        """
        removed = False
        points = [hook_point] if hook_point else list(HookPoint)
        
        for point in points:
            before_count = len(self._hooks[point])
            self._hooks[point] = [h for h in self._hooks[point] if h.name != name]
            if len(self._hooks[point]) < before_count:
                removed = True
        
        return removed
    
    def enable_hook(self, name: str, enabled: bool = True) -> bool:
        """Enable or disable a hook by name."""
        found = False
        for hooks in self._hooks.values():
            for hook in hooks:
                if hook.name == name:
                    hook.enabled = enabled
                    found = True
        return found
    
    def get_hooks(self, hook_point: HookPoint) -> List[HookRegistration]:
        """Get all registered hooks for a point."""
        return list(self._hooks.get(hook_point, []))
    
    def get_all_hooks(self) -> Dict[HookPoint, List[HookRegistration]]:
        """Get all registered hooks."""
        return dict(self._hooks)
    
    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------
    
    async def execute(
        self,
        hook_point: HookPoint,
        context: HookContext,
    ) -> HookResult:
        """
        Execute all hooks for a given hook point.
        
        Hooks are executed in priority order. Results are aggregated:
        - SYNTHESIZE: First synthesize wins, stops execution
        - SKIP: First skip wins, stops execution
        - ABORT: First abort wins, stops execution
        - MODIFY: All modifications are merged
        - CONTINUE: Default, execution continues
        
        Args:
            hook_point: Which hooks to execute
            context: Execution context to pass to hooks
        
        Returns:
            Aggregated result from all hooks
        """
        hooks = [h for h in self._hooks.get(hook_point, []) if h.enabled]
        
        if not hooks:
            return HookResult(action=HookAction.CONTINUE)
        
        # Ensure context has correct hook point
        context.hook_point = hook_point
        
        # Aggregated result
        aggregated = HookResult(action=HookAction.CONTINUE)
        aggregated_metadata: Dict[str, Any] = {}
        
        for hook in hooks:
            try:
                result = await self._execute_single_hook(hook, context)
                
                # Handle terminal actions
                if result.action in (HookAction.SYNTHESIZE, HookAction.SKIP, HookAction.ABORT):
                    logger.debug(f"Hook '{hook.name}' returned terminal action: {result.action.name}")
                    result.metadata.update(aggregated_metadata)
                    return result
                
                # Aggregate MODIFY actions
                if result.action == HookAction.MODIFY:
                    aggregated.action = HookAction.MODIFY
                    
                    if result.modified_messages is not None:
                        aggregated.modified_messages = result.modified_messages
                        context.messages = result.modified_messages
                    
                    if result.modified_tools is not None:
                        aggregated.modified_tools = result.modified_tools
                        context.tools = result.modified_tools
                    
                    if result.modified_tool_calls is not None:
                        aggregated.modified_tool_calls = result.modified_tool_calls
                        context.tool_calls = result.modified_tool_calls
                    
                    if result.modified_tool_args is not None:
                        aggregated.modified_tool_args = result.modified_tool_args
                        context.tool_args = result.modified_tool_args
                
                # Merge metadata
                aggregated_metadata.update(result.metadata)
                
            except Exception as e:
                logger.warning(f"Hook '{hook.name}' failed: {e}")
                self._record_error(hook.name, e)
                
                if self._fail_fast:
                    raise HookError(hook.name, hook_point, e) from e
        
        aggregated.metadata = aggregated_metadata
        return aggregated
    
    async def _execute_single_hook(
        self,
        hook: HookRegistration,
        context: HookContext,
    ) -> HookResult:
        """Execute a single hook, handling sync/async."""
        import time
        start = time.perf_counter()
        
        try:
            if inspect.iscoroutinefunction(hook.hook_fn):
                result = await hook.hook_fn(context)
            else:
                # Run sync hook in executor to not block
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, hook.hook_fn, context)
            
            # Ensure we have a valid result
            if result is None:
                result = HookResult()
            elif not isinstance(result, HookResult):
                result = HookResult(metadata={"raw_result": result})
            
            elapsed = time.perf_counter() - start
            self._record_execution(hook.name, elapsed, success=True)
            
            return result
            
        except Exception as e:
            elapsed = time.perf_counter() - start
            self._record_execution(hook.name, elapsed, success=False, error=str(e))
            raise
    
    # -------------------------------------------------------------------------
    # Convenience Methods for Common Patterns
    # -------------------------------------------------------------------------
    
    async def execute_before_model(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        session_id: Optional[str] = None,
        turn_number: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """Convenience method for BEFORE_MODEL hooks."""
        context = HookContext(
            hook_point=HookPoint.BEFORE_MODEL,
            messages=messages,
            tools=tools,
            session_id=session_id,
            turn_number=turn_number,
            metadata=metadata or {},
        )
        return await self.execute(HookPoint.BEFORE_MODEL, context)
    
    async def execute_after_model(
        self,
        messages: List[Dict[str, Any]],
        model_response: "NormalizedMessage",
        tool_calls: List[Dict[str, Any]],
        session_id: Optional[str] = None,
        turn_number: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """Convenience method for AFTER_MODEL hooks."""
        context = HookContext(
            hook_point=HookPoint.AFTER_MODEL,
            messages=messages,
            model_response=model_response,
            tool_calls=tool_calls,
            session_id=session_id,
            turn_number=turn_number,
            metadata=metadata or {},
        )
        return await self.execute(HookPoint.AFTER_MODEL, context)
    
    async def execute_before_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """Convenience method for BEFORE_TOOL_EXECUTION hooks."""
        context = HookContext(
            hook_point=HookPoint.BEFORE_TOOL_EXECUTION,
            tool_name=tool_name,
            tool_args=tool_args,
            session_id=session_id,
            metadata=metadata or {},
        )
        return await self.execute(HookPoint.BEFORE_TOOL_EXECUTION, context)
    
    async def execute_after_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_result: Any,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HookResult:
        """Convenience method for AFTER_TOOL_EXECUTION hooks."""
        context = HookContext(
            hook_point=HookPoint.AFTER_TOOL_EXECUTION,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            session_id=session_id,
            metadata=metadata or {},
        )
        return await self.execute(HookPoint.AFTER_TOOL_EXECUTION, context)
    
    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------
    
    def _record_execution(
        self,
        hook_name: str,
        elapsed: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record hook execution statistics."""
        if hook_name not in self._execution_stats:
            self._execution_stats[hook_name] = {
                "executions": 0,
                "successes": 0,
                "failures": 0,
                "total_time": 0.0,
                "last_error": None,
            }
        
        stats = self._execution_stats[hook_name]
        stats["executions"] += 1
        stats["total_time"] += elapsed
        
        if success:
            stats["successes"] += 1
        else:
            stats["failures"] += 1
            stats["last_error"] = error
    
    def _record_error(self, hook_name: str, error: Exception) -> None:
        """Record a hook error."""
        if hook_name not in self._execution_stats:
            self._execution_stats[hook_name] = {
                "executions": 0,
                "successes": 0,
                "failures": 0,
                "total_time": 0.0,
                "last_error": None,
            }
        self._execution_stats[hook_name]["last_error"] = str(error)
    
    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get execution statistics for all hooks."""
        return dict(self._execution_stats)
    
    def reset_stats(self) -> None:
        """Reset all execution statistics."""
        self._execution_stats.clear()


# Global default hook system
_default_hook_system: Optional[HookSystem] = None


def get_default_hook_system() -> HookSystem:
    """Get or create the default global hook system."""
    global _default_hook_system
    if _default_hook_system is None:
        _default_hook_system = HookSystem()
    return _default_hook_system


def set_default_hook_system(system: HookSystem) -> None:
    """Set the default global hook system."""
    global _default_hook_system
    _default_hook_system = system
