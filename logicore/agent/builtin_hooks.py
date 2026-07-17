"""Built-in hooks for agent verification and self-healing.

Modeled after Claude Code's stop hooks (``src/query/stopHooks.ts``) which:
1. Block continuation if issues found
2. Extract memories for future sessions
3. Suggest prompts for next steps
4. Run verification checks

These hooks are registered by default and can be disabled via configuration.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from logicore.runtime.hooks.types import (
    HookPoint,
    HookAction,
    HookContext,
    HookResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Stop Hooks (AFTER_TURN)
# =============================================================================

async def verification_stop_hook(ctx: HookContext) -> HookResult:
    """Verify that the agent accomplished something meaningful this turn.
    
    This hook checks if the agent:
    1. Made progress (tools were executed)
    2. Tasks are actually being completed (not just tool execution)
    3. Didn't just repeat the same actions
    4. Didn't encounter too many errors
    
    Uses task completion status for dynamic verification — not rule-based.
    If issues are found, it injects a message for the LLM to address.
    """
    metadata = ctx.metadata or {}
    successful_tools = metadata.get("successful_tools", 0)
    tools_used = metadata.get("tools_used", [])
    iteration = metadata.get("iteration", 1)
    task_summary = metadata.get("task_summary", {})
    
    warnings = []
    
    # Check 1: No tools executed — might be stuck
    if successful_tools == 0 and iteration > 1:
        warnings.append(
            "No tools were executed this turn. "
            "Consider using tools to make progress on the task."
        )
    
    # Check 2: Tasks remain open after many iterations — verify completion
    pending = task_summary.get("pending", 0)
    in_progress = task_summary.get("in_progress", 0)
    completed = task_summary.get("completed", 0)
    total = pending + in_progress + completed
    
    if total > 0 and completed < total and iteration >= 3:
        # Tasks exist but not all completed — inject verification prompt
        open_tasks = pending + in_progress
        warnings.append(
            f"There are still {open_tasks} open task(s) ({pending} pending, "
            f"{in_progress} in progress) out of {total} total. "
            f"Before finishing, verify that your response actually addresses "
            f"the remaining tasks. If you believe all work is done, "
            f"explicitly mark tasks as completed using task_update."
        )
    
    # Check 3: Same tools repeated without progress
    if len(tools_used) >= 3:
        # Check if the same tool was called multiple times with similar results
        tool_counts = {}
        for t in tools_used:
            tool_counts[t] = tool_counts.get(t, 0) + 1
        for tool_name, count in tool_counts.items():
            if count >= 3:
                warnings.append(
                    f"Tool '{tool_name}' was called {count} times this turn. "
                    f"If it keeps failing or producing similar output, "
                    f"try a fundamentally different approach."
                )
                break
    
    if warnings:
        return HookResult(
            action=HookAction.MODIFY,
            metadata={
                "verification_warning": " ".join(warnings)
            }
        )
    
    return HookResult(action=HookAction.CONTINUE)


async def error_rate_stop_hook(ctx: HookContext) -> HookResult:
    """Monitor error rates and inject warnings if too many failures.
    
    This hook tracks tool failure rates and warns the LLM if it's
    experiencing too many errors, prompting it to change approach.
    """
    metadata = ctx.metadata or {}
    tools_used = metadata.get("tools_used", [])
    
    # Count recent errors from messages (simple heuristic)
    error_count = 0
    for msg in ctx.messages[-10:]:  # Check last 10 messages
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and "error" in content.lower():
                error_count += 1
    
    if error_count >= 3:
        return HookResult(
            action=HookAction.MODIFY,
            metadata={
                "error_warning": f"Detected {error_count} recent errors. "
                "Consider a different approach or ask for clarification."
            }
        )
    
    return HookResult(action=HookAction.CONTINUE)


async def memory_extraction_stop_hook(ctx: HookContext) -> HookResult:
    """Extract operational memories from the conversation.
    
    This hook analyzes the conversation for:
    - User corrections and preferences
    - Successful approaches
    - Failed approaches to avoid
    
    Memories are stored in session state for cross-session learning.
    """
    # This is a fire-and-forget hook - we just mark that extraction should happen
    # The actual extraction happens in the background
    return HookResult(
        action=HookAction.CONTINUE,
        metadata={
            "should_extract_memories": True,
            "turn_number": ctx.turn_number,
        }
    )


# =============================================================================
# Tool Execution Hooks
# =============================================================================

async def tool_error_injection_hook(ctx: HookContext) -> HookResult:
    """Inject error context into tool results for better LLM reasoning.
    
    This hook formats tool errors to help the LLM understand what went wrong
    and how to recover, similar to Claude Code's ``formatToolError()``.
    """
    if ctx.tool_result is None:
        return HookResult(action=HookAction.CONTINUE)
    
    result = ctx.tool_result
    if not isinstance(result, dict):
        return HookResult(action=HookAction.CONTINUE)
    
    # Only process failed results
    if result.get("success", True):
        return HookResult(action=HookAction.CONTINUE)
    
    # Enhance error message with context
    error = result.get("error", "")
    tool_name = ctx.tool_name or "unknown"
    
    # Add tool-specific guidance
    guidance = ""
    if "not found" in str(error).lower() or "no such file" in str(error).lower():
        guidance = f"\nHint: The file/directory may not exist. Check the path and try again."
    elif "permission denied" in str(error).lower():
        guidance = f"\nHint: Permission was denied. You may need elevated privileges."
    elif "timeout" in str(error).lower():
        guidance = f"\nHint: The operation timed out. Try a simpler approach or break it into smaller steps."
    elif "invalid" in str(error).lower():
        guidance = f"\nHint: The input was invalid. Check the format and try again."
    
    if guidance:
        enhanced_error = f"{error}{guidance}"
        result["error"] = enhanced_error
        result["_enhanced_error"] = True
    
    return HookResult(
        action=HookAction.MODIFY,
        tool_result=result,
    )


# =============================================================================
# Hook Registration
# =============================================================================

def register_builtin_hooks(hook_system, enabled: Optional[Dict[str, bool]] = None):
    """Register all built-in hooks with the hook system.
    
    Args:
        hook_system: HookSystem instance to register with
        enabled: Optional dict of hook_name -> enabled status
    """
    if enabled is None:
        enabled = {}
    
    # Stop hooks (AFTER_TURN)
    hook_system.add_hook(
        name="verification_stop",
        hook_point=HookPoint.AFTER_TURN,
        hook_fn=verification_stop_hook,
        priority=100,
        enabled=enabled.get("verification_stop", True),
        description="Verify agent made progress this turn",
    )
    
    hook_system.add_hook(
        name="error_rate_stop",
        hook_point=HookPoint.AFTER_TURN,
        hook_fn=error_rate_stop_hook,
        priority=110,
        enabled=enabled.get("error_rate_stop", True),
        description="Monitor error rates and warn if too high",
    )
    
    hook_system.add_hook(
        name="memory_extraction_stop",
        hook_point=HookPoint.AFTER_TURN,
        hook_fn=memory_extraction_stop_hook,
        priority=200,
        enabled=enabled.get("memory_extraction_stop", True),
        description="Extract operational memories from conversation",
    )
    
    # Tool execution hooks
    hook_system.add_hook(
        name="tool_error_injection",
        hook_point=HookPoint.AFTER_TOOL_EXECUTION,
        hook_fn=tool_error_injection_hook,
        priority=50,
        enabled=enabled.get("tool_error_injection", True),
        description="Enhance tool error messages with recovery guidance",
    )
    
    logger.debug(f"Registered {len(hook_system.get_hooks(HookPoint.AFTER_TURN))} stop hooks")
    logger.debug(f"Registered {len(hook_system.get_hooks(HookPoint.AFTER_TOOL_EXECUTION))} tool hooks")
