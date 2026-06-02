"""
Hooks Module — Execution lifecycle hooks for agent customization.

Provides interception points throughout the agent execution lifecycle:
- BEFORE_MODEL: Modify messages/tools before LLM call
- AFTER_MODEL: Process/intercept model responses
- BEFORE_TOOL_EXECUTION: Validate/modify tool calls
- AFTER_TOOL_EXECUTION: Process tool results
- And more...

Usage:
    from logicore.runtime.hooks import HookSystem, HookPoint, HookContext, HookResult
    
    hooks = HookSystem()
    
    @hooks.register(HookPoint.BEFORE_MODEL, priority=10)
    async def inject_context(ctx: HookContext) -> HookResult:
        # Add custom system message
        ctx.messages.insert(0, {
            "role": "system",
            "content": "Additional context here"
        })
        return HookResult(
            action=HookAction.MODIFY,
            modified_messages=ctx.messages
        )
"""

from .types import (
    HookPoint,
    HookAction,
    HookContext,
    HookResult,
    HookRegistration,
    HookFn,
    SyncHookFn,
    AsyncHookFn,
    HookError,
)

from .system import (
    HookSystem,
    get_default_hook_system,
    set_default_hook_system,
)

__all__ = [
    # Types
    "HookPoint",
    "HookAction",
    "HookContext",
    "HookResult",
    "HookRegistration",
    "HookFn",
    "SyncHookFn",
    "AsyncHookFn",
    "HookError",
    # System
    "HookSystem",
    "get_default_hook_system",
    "set_default_hook_system",
]
