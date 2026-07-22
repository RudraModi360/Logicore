"""
ToolPipeline: Encapsulates the per-tool-call execution pipeline.

Extracted from ChatOrchestrator to separate concerns:
- ChatOrchestrator: Loop control, LLM interaction, response handling
- ToolPipeline: Per-tool-call orchestration (guardrails, hooks, execution,
  loop detection, operational memory, feedback, session tracking)

Phase 2d of the orchestrator decomposition.
"""

import json
import time
import logging
import traceback
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from logicore.runtime.hooks.types import HookPoint, HookAction, HookContext
from logicore.agent.tool_guardrails import append_toolguard_guidance
from logicore.agent.turn_retry_state import TurnRetryState, TransitionReason

if TYPE_CHECKING:
    from logicore.agent.agent_protocol import AgentProtocol
    from logicore.agent.tool_guardrails import ToolCallGuardrailController
    from logicore.runtime.hooks.system import HookSystem
    from logicore.agent.operational_memory import OperationalMemoryManager
    from logicore.agent.feedback_handler import FeedbackHandler
    from logicore.stream.emitter import StreamEmitter
    from logicore.stream.events import StreamEvent

logger = logging.getLogger(__name__)


class ToolPipeline:
    """
    Per-tool-call execution pipeline.

    Encapsulates the full lifecycle of a single tool call:
    1. Loop detection (TOOL_CALL event)
    2. Loop recovery check
    3. Cooldown check
    4. Tool guardrails pre-check
    5. BEFORE_TOOL_EXECUTION hooks
    6. Tool execution via ToolExecutor
    7. AFTER_TOOL_EXECUTION hooks
    8. Tool guardrails post-check
    9. Operational memory recording
    10. Feedback handling
    11. Loop detection (TOOL_RESULT event)
    12. Loop recovery check
    13. Session tracking (add_tool_result)
    """

    def __init__(
        self,
        agent: "AgentProtocol",
        tool_guardrails: "ToolCallGuardrailController",
        hook_system: "HookSystem",
        operational_memory: "OperationalMemoryManager",
        feedback_handler: "FeedbackHandler",
        turn_retry_state: "TurnRetryState",
        debug: bool = False,
    ):
        self.agent = agent
        self.tool_guardrails = tool_guardrails
        self._hook_system = hook_system
        self._operational_memory = operational_memory
        self._feedback_handler = feedback_handler
        self._turn_retry_state = turn_retry_state
        self.debug = debug

        # Halt decision set by guardrails when repeated failures exceed limits.
        # Caller checks this after the pipeline loop to decide whether to stop.
        self.halt_decision = None

    async def execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        session,
        session_id: str,
        all_tools: Optional[List[Dict[str, Any]]],
        iteration: int,
        turn_number: int,
        emitter: Optional["StreamEmitter"],
        callbacks: Dict[str, Any],
        local_result_cache: Dict[str, Dict[str, Any]],
        injected_hints: List[str],
        tools_used_this_chat: List[str],
        last_tool_name: Optional[str],
    ) -> Optional[tuple]:
        """
        Execute all tool calls in a single LLM response.

        Returns None to continue the main loop, or ("terminate", message) to
        stop execution.
        """
        from logicore.runtime.loop_detection.engine import AgentEventType
        from logicore.stream.events import StreamEvent as _SE, StreamEventType as _SET

        for tc in tool_calls:
            name, args, tc_id = self._extract_tool_call_details(tc)
            if not name:
                continue

            result = await self._execute_single_pipeline(
                name=name,
                args=args,
                tc_id=tc_id,
                session=session,
                session_id=session_id,
                all_tools=all_tools,
                iteration=iteration,
                turn_number=turn_number,
                emitter=emitter,
                callbacks=callbacks,
                local_result_cache=local_result_cache,
                injected_hints=injected_hints,
                tools_used_this_chat=tools_used_this_chat,
                last_tool_name=last_tool_name,
            )

            if result is not None:
                return result

        return None

    async def _execute_single_pipeline(
        self,
        name: str,
        args,
        tc_id: Optional[str],
        session,
        session_id: str,
        all_tools: Optional[List[Dict[str, Any]]],
        iteration: int,
        turn_number: int,
        emitter: Optional["StreamEmitter"],
        callbacks: Dict[str, Any],
        local_result_cache: Dict[str, Dict[str, Any]],
        injected_hints: List[str],
        tools_used_this_chat: List[str],
        last_tool_name: Optional[str],
    ) -> Optional[tuple]:
        """
        Run the full pipeline for one tool call.

        Returns None to continue, or ("terminate", message) to stop.
        """
        from logicore.runtime.loop_detection.engine import AgentEventType
        from logicore.stream.events import StreamEvent as _SE, StreamEventType as _SET

        # 1. Loop detection: feed the tool call
        call_detected = await self._check_loop(
            AgentEventType.TOOL_CALL, session_id,
            tool_name=name, tool_args=args,
        )
        rec = await self._maybe_recover_loop(
            call_detected, session, session_id,
            tools_used_this_chat, last_tool_name,
        )
        if rec and rec[0] == "terminate":
            return rec

        # 2. Cooldown check
        engine = getattr(self.agent, "_loop_engine", None)
        if engine and engine.is_tool_cooled_down(session_id, name):
            blocked = {
                "success": False,
                "error": (
                    f"Tool '{name}' is temporarily disabled (loop cooldown). "
                    f"Use a different tool or approach."
                ),
            }
            tool_msg = {
                "role": "tool",
                "name": name,
                "content": self.agent.tool_executor.normalize_tool_result(name, blocked),
            }
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

        if self.debug:
            args_preview = str(args)[:200] if args else "{}"
            logger.debug(f"[Tool] Calling: {name}({args_preview})")

        if emitter:
            emitter.emit(_SE.create(
                _SET.TOOL_CALL_START,
                {
                    "name": name,
                    "call_id": tc_id,
                    "args": args if isinstance(args, dict) else {},
                    "iteration": iteration,
                },
            ))

        # 3-12. Core pipeline: guardrails, hooks, execution, memory, feedback.
        # Wrapped in specific exception handling so a bug in any layer
        # degrades gracefully instead of crashing the entire chat loop.
        try:
            # 3. Tool guardrails: pre-execution check
            guardrail_decision = self.tool_guardrails.before_call(name, args)
            if not guardrail_decision.allows_execution:
                result = self.agent.tool_executor.normalize_tool_result(
                    name,
                    {"success": False, "error": guardrail_decision.message}
                )
                result["_guardrail_blocked"] = True
                result["_guardrail_decision"] = guardrail_decision.to_metadata()

                tool_msg = {
                    "role": "tool",
                    "name": name,
                    "content": self.agent._serialize_tool_result_for_model(name, result),
                }
                if tc_id:
                    tool_msg["tool_call_id"] = tc_id
                session.add_message(tool_msg)

                if self.debug:
                    logger.debug(f"[ToolGuardrails] BLOCKED '{name}': {guardrail_decision.code}")

                if guardrail_decision.should_halt:
                    self.halt_decision = guardrail_decision

                return None

            # 4. BEFORE_TOOL_EXECUTION hooks
            before_tool_ctx = HookContext(
                hook_point=HookPoint.BEFORE_TOOL_EXECUTION,
                messages=session.messages,
                tools=all_tools or [],
                tool_name=name,
                tool_args=args,
                session_id=session_id,
                turn_number=turn_number,
                metadata={"iteration": iteration, "call_id": tc_id}
            )
            before_tool_result = await self._hook_system.execute(
                HookPoint.BEFORE_TOOL_EXECUTION, before_tool_ctx
            )

            if before_tool_result.action == HookAction.SKIP:
                skip_reason = before_tool_result.skip_reason or "Tool execution skipped by hook"
                result = self.agent.tool_executor.normalize_tool_result(
                    name, {"success": False, "error": skip_reason}
                )
                tool_msg = {
                    "role": "tool",
                    "name": name,
                    "content": self.agent._serialize_tool_result_for_model(name, result),
                }
                if tc_id:
                    tool_msg["tool_call_id"] = tc_id
                session.add_message(tool_msg)
                return None
            elif before_tool_result.action == HookAction.ABORT:
                abort_msg = before_tool_result.metadata.get("reason", "Hook aborted tool execution")
                self._clear_injected_hints(session, injected_hints)
                if emitter:
                    emitter.emit(_SE.create(_SET.DONE, {"content": abort_msg}))
                return ("terminate", abort_msg)
            elif before_tool_result.action == HookAction.MODIFY:
                if before_tool_result.modified_tool_args is not None:
                    args = before_tool_result.modified_tool_args

            # 5. Execute tool
            result = await self._execute_tool(
                name, args, tc_id, session, session_id,
                callbacks, local_result_cache
            )

            # 6. AFTER_TOOL_EXECUTION hooks
            after_tool_ctx = HookContext(
                hook_point=HookPoint.AFTER_TOOL_EXECUTION,
                messages=session.messages,
                tools=all_tools or [],
                tool_name=name,
                tool_args=args,
                tool_result=result,
                session_id=session_id,
                turn_number=turn_number,
                metadata={"iteration": iteration, "call_id": tc_id}
            )
            after_tool_result = await self._hook_system.execute(
                HookPoint.AFTER_TOOL_EXECUTION, after_tool_ctx
            )

            if after_tool_result.action == HookAction.MODIFY:
                if after_tool_result.tool_result is not None:
                    result = after_tool_result.tool_result

            # 7. Tool guardrails: post-execution observation
            is_error = not bool(result.get("success", True))
            guardrail_decision = self.tool_guardrails.after_call(
                name, args, str(result.get("content") or result.get("error") or ""),
                failed=is_error,
            )

            if guardrail_decision.action in {"warn", "halt"}:
                result_str = self.agent._serialize_tool_result_for_model(name, result)
                result_str = append_toolguard_guidance(result_str, guardrail_decision)
                if isinstance(result, dict):
                    result["_guardrail_guidance"] = guardrail_decision.message

            if guardrail_decision.should_halt:
                self.halt_decision = guardrail_decision

            # 8. Operational memory: record failure patterns
            self._record_operational_memory(name, result, is_error, injected_hints, session)

            # 9. Feedback handling: inject hints for tool failures
            self._handle_feedback(name, args, result, is_error, session, injected_hints)

            if self.debug:
                success = result.get("success", True)
                content_preview = str(result.get("content", ""))[:150]
                status = "OK" if success else "FAILED"
                logger.debug(f"[Tool] Result: {name} -> {status} | {content_preview}")

            if emitter:
                preview = str(result.get("content", ""))[:280]
                emitter.emit(_SE.create(
                    _SET.TOOL_CALL_END,
                    {
                        "name": name,
                        "call_id": tc_id,
                        "success": bool(result.get("success", True)),
                        "preview": preview,
                        "iteration": iteration,
                    },
                ))

            # 10. Track success/failure stats
            if result.get("success", True):
                tools_used_this_chat.append(name)

            # 11. Session tracking
            args_hash = str(hash(json.dumps(args, sort_keys=True, default=str)))[:16] if args else None
            session.add_tool_result(
                tool_name=name,
                success=bool(result.get("success", True)),
                result_summary=str(result.get("content") or result.get("error") or "")[:200],
                args_hash=args_hash
            )

            if is_error:
                self._turn_retry_state.record_transition(
                    TransitionReason.TOOL_USE,
                    detail=f"Tool '{name}' failed: {str(result.get('error', ''))[:100]}"
                )
                
                # Inject recovery guidance for common failures
                error_msg = str(result.get('error', '')).lower()
                recovery_hint = self._get_recovery_hint(name, error_msg)
                if recovery_hint:
                    result["_recovery_hint"] = recovery_hint

            # 12. Loop detection: feed tool result
            res_detected = await self._check_loop(
                AgentEventType.TOOL_RESULT, session_id,
                tool_name=name,
                tool_result=str(result.get("content") or result.get("error") or ""),
                tool_success=bool(result.get("success", True)),
            )
            rec = await self._maybe_recover_loop(
                res_detected, session, session_id,
                tools_used_this_chat, last_tool_name,
            )
            if rec and rec[0] == "terminate":
                return rec

            return None

        except (NameError, ImportError) as e:
            logger.error(
                f"[ToolPipeline] Import resolution error in pipeline for '{name}': {e}\n"
                f"{traceback.format_exc()}"
            )
            error_result = self.agent.tool_executor.normalize_tool_result(
                name,
                {"success": False, "error": f"Internal pipeline error: {type(e).__name__}. Try a different approach."}
            )
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, error_result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

        except (TypeError, ValueError, KeyError) as e:
            logger.error(
                f"[ToolPipeline] Data error in pipeline for '{name}': {e}\n"
                f"{traceback.format_exc()}"
            )
            error_result = self.agent.tool_executor.normalize_tool_result(
                name,
                {"success": False, "error": f"Pipeline data error ({type(e).__name__}): {str(e)[:200]}"}
            )
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, error_result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

        except AttributeError as e:
            logger.error(
                f"[ToolPipeline] Attribute error in pipeline for '{name}': {e}\n"
                f"{traceback.format_exc()}"
            )
            error_result = self.agent.tool_executor.normalize_tool_result(
                name,
                {"success": False, "error": f"Internal configuration error: missing attribute '{str(e).split()[-1]}'. Try a different tool."}
            )
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, error_result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

        except RuntimeError as e:
            logger.error(
                f"[ToolPipeline] Runtime error in pipeline for '{name}': {e}\n"
                f"{traceback.format_exc()}"
            )
            error_result = self.agent.tool_executor.normalize_tool_result(
                name,
                {"success": False, "error": f"Runtime error ({type(e).__name__}): {str(e)[:200]}"}
            )
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, error_result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

        except Exception as e:
            logger.error(
                f"[ToolPipeline] Unexpected error in pipeline for '{name}': {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )
            error_result = self.agent.tool_executor.normalize_tool_result(
                name,
                {"success": False, "error": f"Unexpected pipeline error ({type(e).__name__}). Try a different approach."}
            )
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, error_result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return None

    # --- Helpers ---

    async def _execute_tool(self, name, args, tc_id, session, session_id,
                            callbacks, local_result_cache):
        """Execute the tool via ToolExecutor with argument parsing and callbacks."""
        args, parse_error = self.agent.tool_executor.parse_tool_arguments(name, args)
        args = self.agent._normalize_tool_paths(session, name, args)

        if parse_error:
            result = self.agent.tool_executor.normalize_tool_result(name, {"success": False, "error": parse_error})
            if callbacks.get("on_tool_end"):
                await self._fire_callback(callbacks["on_tool_end"], session_id, name, result)
            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, result)}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return result

        if callbacks.get("on_tool_start"):
            await self._fire_callback(callbacks["on_tool_start"], session_id, name, args)

        result = await self.agent.tool_executor.execute(name, args, session_id, local_result_cache)

        if callbacks.get("on_tool_end"):
            await self._fire_callback(callbacks["on_tool_end"], session_id, name, result)

        self.agent._update_tool_directory_context(session, name, args, result)

        tool_msg = {
            "role": "tool",
            "name": name,
            "content": self.agent._serialize_tool_result_for_model(name, result)
        }
        if tc_id:
            tool_msg["tool_call_id"] = tc_id
        session.add_message(tool_msg)

        is_error = not bool(result.get("success", True))

        if is_error and "_classification" in result:
            classification = result["_classification"]
            recovery_action = classification.get("recovery_action", "")

            if recovery_action == "inject_signal":
                error_msg = classification.get("error", "")
                signal = (
                    f"The tool '{name}' failed with a deterministic error. "
                    f"Error: {error_msg}. "
                    f"Try a different approach — do NOT retry the same call."
                )
                session.add_message({"role": "system", "content": signal})

            elif recovery_action == "compress_context":
                signal = (
                    f"The tool '{name}' failed because the request was too large. "
                    f"Consider breaking the request into smaller pieces or "
                    f"reducing the amount of data being processed."
                )
                session.add_message({"role": "system", "content": signal})

        if is_error:
            self.agent.execution_log.append(f"Tool {name} FAILED: {result.get('error', 'Unknown error')}")
        else:
            self.agent.execution_log.append(f"Tool {name} SUCCEEDED")

        if name == "load_skill" and not is_error:
            skill_name = args.get("skill_name")
            if skill_name and hasattr(self.agent, '_skill_index_entries'):
                if skill_name in self.agent._skill_index_entries:
                    skills_dir, entry = self.agent._skill_index_entries[skill_name]
                    from logicore.skills.loader import SkillLoader
                    skill = SkillLoader.load_skill_by_index(skills_dir, skill_name)
                    if skill:
                        self.agent._register_skill_tools(skill)
                        self.agent._rebuild_system_prompt_with_tools()

        return result

    def _record_operational_memory(self, name, result, is_error, injected_hints, session):
        """Record failure/success patterns in operational memory."""
        if is_error:
            error_msg = str(result.get('error', ''))
            _cls = result.get("_classification", {})
            error_type = _cls.get("error_category", "unknown") if _cls else "unknown"
            _recovery = _cls.get("recovery_action") if _cls else None

            pattern = self._operational_memory.record_tool_failure(
                tool_name=name,
                error_type=error_type,
                error_message=error_msg,
                recovery_action=_recovery,
            )

            self._operational_memory._session_state.record_recovery_attempt(
                error_type=error_type,
                tool_name=name,
            )

            if self._operational_memory.should_escalate_recovery(
                error_type=error_type,
                tool_name=name,
                max_attempts=3,
            ):
                escalation_msg = (
                    f"Recovery for {error_type} on '{name}' has failed multiple times. "
                    f"Consider a fundamentally different approach instead of retrying."
                )
                self.agent.context_engine.inject_hint(session.messages, escalation_msg)
                injected_hints.append(escalation_msg)

                if self.debug:
                    logger.debug(f"[OperationalMemory] Escalating recovery for {pattern.pattern_id}")
        else:
            if name in [p.tool_name for p in self._operational_memory._session_state.failure_patterns.values()]:
                self._operational_memory.record_tool_success(
                    tool_name=name,
                    error_type="previous_failure",
                )

    def _handle_feedback(self, name, args, result, is_error, session, injected_hints):
        """Inject feedback hints for tool failures."""
        if is_error:
            error_msg = str(result.get('error', ''))
            feedback = self._feedback_handler.handle_tool_failed(
                tool_name=name,
                error=error_msg,
                session=session,
            )
            for injection in feedback.injected_hints:
                self.agent.context_engine.inject_hint(session.messages, injection.message)
                injected_hints.append(injection.message)
            if self.debug and feedback.has_injections:
                logger.debug(f"[FeedbackHandler] Injected tool failure hint for '{name}'")

        self._feedback_handler.set_previous_action(
            f"Called tool '{name}' with args: {str(args)[:100]}"
        )

    @staticmethod
    def _extract_tool_call_details(tc) -> tuple:
        """Extract name, args, id from a tool call."""
        try:
            if isinstance(tc, dict):
                func = tc.get('function')
                if not isinstance(func, dict):
                    return None, None, None
                return func.get('name'), func.get('arguments', {}), tc.get('id')
            else:
                func = getattr(tc, 'function', None)
                return (
                    getattr(func, 'name', None) if func else None,
                    getattr(func, 'arguments', {}) if func else {},
                    getattr(tc, 'id', None)
                )
        except Exception as e:
            logger.error(f"Failed to parse tool call: {e}")
            return None, None, None

    def _clear_injected_hints(self, session, injected_hints: List[str]) -> None:
        """Remove transient hints injected during this turn from session history."""
        for hint in injected_hints:
            try:
                self.agent.context_engine.remove_hint(session.messages, hint)
            except Exception:
                pass

    async def _check_loop(self, event_type, session_id: str, **kwargs):
        """Feed one execution event to the loop-detection engine (if enabled)."""
        engine = getattr(self.agent, "_loop_engine", None)
        if not engine or engine.is_disabled(session_id):
            return None
        from logicore.runtime.loop_detection.engine import AgentEvent
        event = AgentEvent(type=event_type, **kwargs)
        try:
            return await engine.check(event, session_id)
        except Exception as e:
            if self.debug:
                logger.warning(f"[ToolPipeline] loop check failed: {e}")
            return None

    async def _maybe_recover_loop(self, result, session, session_id: str,
                                  tools_used, last_tool):
        """Turn a detected loop into a recovery action."""
        if not result or not result.detected:
            return None
        engine = getattr(self.agent, "_loop_engine", None)
        if not engine:
            return None
        from logicore.runtime.loop_detection.recovery import (
            RecoveryActionType, get_recovery_action,
        )
        session_context = {
            "tools_used": list(tools_used),
            "last_tool_name": last_tool,
            "model_name": getattr(self.agent, "model_name", ""),
        }
        action = get_recovery_action(
            result.loop_type.value if result.loop_type else "default",
            result.detail,
            result.suggested_escalation,
            session_context,
        )
        if action.action_type in (RecoveryActionType.TERMINATE, RecoveryActionType.SWITCH_MODEL):
            return ("terminate", action.guidance_message or
                    "Stopped: a persistent loop was detected and could not be resolved.")
        if action.action_type == RecoveryActionType.COOL_DOWN_TOOL and action.tool_name:
            engine.apply_tool_cooldown(session_id, action.tool_name)
        if action.guidance_message:
            session.add_message({"role": "system", "content": action.guidance_message})
        return ("continue", None)

    async def _fire_callback(self, callback, *args):
        """Fire a callback (sync or async)."""
        import inspect
        if inspect.iscoroutinefunction(callback):
            await callback(*args)
        else:
            callback(*args)

    def _get_recovery_hint(self, tool_name: str, error_msg: str) -> str:
        """Generate recovery guidance based on tool and error type."""
        hints = {
            "not found": "File or directory not found. Check the path and try again with a different location.",
            "permission denied": "Permission denied. Try using a different approach or check file permissions.",
            "timeout": "Operation timed out. Try a simpler approach or break into smaller steps.",
            "connection": "Connection error. Check network connectivity and retry.",
            "invalid": "Invalid input. Check the format and try again.",
            "already exists": "File or resource already exists. Use a different name or overwrite if intended.",
            "no such file": "File does not exist. Verify the path is correct.",
            "unicode": "Encoding error. Try reading the file with a different encoding.",
        }
        
        for pattern, hint in hints.items():
            if pattern in error_msg:
                return hint
        
        # Tool-specific hints
        tool_hints = {
            "bash": "Command failed. Check the command syntax and try an alternative approach.",
            "write_file": "Write failed. Check if the directory exists and you have write permissions.",
            "read_file": "Read failed. Check if the file exists and is readable.",
            "list_files": "List failed. Check if the directory exists.",
        }
        
        for tool, hint in tool_hints.items():
            if tool in tool_name.lower():
                return hint
        
        return ""
