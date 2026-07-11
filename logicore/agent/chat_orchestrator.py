"""
ChatOrchestrator: Extracted chat loop from Agent god class.

Handles the main chat loop, LLM interaction, tool execution orchestration,
error handling, and response processing.

Planning and task tracking are driven by the LLM itself through the system
prompt (the task tools are always available). There is no separate routing
"brain" making plan/act decisions out-of-band - the model decides when to
create and work through tasks, exactly like production agent systems.
"""

import time
import json
import inspect
import asyncio
from typing import Dict, Any, Callable, List, Optional, Union
import logging

from logicore.gateway.gateway import NormalizedMessage
from logicore.security.input_sanitizer import InputSanitizer, InjectionAction
from logicore.stream.events import StreamEvent, StreamEventType
from logicore.stream.emitter import StreamEmitter
from logicore.runtime.loop_detection.engine import AgentEventType

logger = logging.getLogger(__name__)


class ChatOrchestrator:
    """
    Manages the chat loop between user, LLM, and tools.

    Extracted from Agent to reduce god class size and improve testability.
    """

    def __init__(
        self,
        agent: Any,
        debug: bool = False,
        input_sanitizer: Optional[InputSanitizer] = None,
    ):
        """
        Args:
            agent: Parent Agent instance (for accessing provider, gateway, context, etc.)
            debug: Enable debug logging
            input_sanitizer: Optional sanitizer for prompt injection protection
        """
        self.agent = agent
        self.debug = debug
        self.input_sanitizer = input_sanitizer or InputSanitizer(action=InjectionAction.WARN)

    def _tool_context_window(self) -> Optional[int]:
        """Resolve the active model's context window for budget gating.

        Falls back gracefully to None (budget module then uses its fixed
        fallback ceiling) so a missing provider never breaks tool assembly.
        """
        try:
            provider = getattr(self.agent, "provider", None)
            model = getattr(self.agent, "model", None) or getattr(self.agent, "model_name", None)
            if provider and hasattr(provider, "get_context_window"):
                ctx = provider.get_context_window()
                if ctx and ctx > 0:
                    return int(ctx)
            from logicore.context_engine.token_estimator import get_model_context_window
            if model:
                return get_model_context_window(model, provider)
        except Exception:
            pass
        return None

    def _tool_budget_config(self) -> Any:
        """Build a ``ToolBudgetConfig`` from env-driven settings, if enabled.

        Opt-in via environment variables (no config-schema change required,
        so existing deployments are unaffected — ``get_all_tools`` becomes a
        passthrough when unset):

          LOGICORE_TOOL_BUDGET_MODE   "off" | "on" | "auto"   (default: off)
          LOGICORE_TOOL_BUDGET_MAX_TOKENS  hard schema-token ceiling (optional)
          LOGICORE_TOOL_BUDGET_PCT    % of context window (default 25)

        Returns None when budget enforcement is not configured.
        """
        try:
            from logicore.config.env import _raw
            raw_mode = _raw("LOGICORE_TOOL_BUDGET_MODE")
            if not raw_mode:
                return None
            raw = {
                "mode": raw_mode,
                "max_schema_tokens": _raw("LOGICORE_TOOL_BUDGET_MAX_TOKENS"),
                "threshold_pct": _raw("LOGICORE_TOOL_BUDGET_PCT", "25"),
            }
            from logicore.tools.tool_budget import ToolBudgetConfig
            return ToolBudgetConfig.from_raw(raw)
        except Exception:
            return None

    def _sanitize_user_input(self, user_input: Union[str, List[Dict[str, Any]]]):
        """
        Sanitize user input for prompt injection.

        Returns a tuple (sanitized_input, blocked). For list/multimodal content
        every string part is scanned individually so injection fences inside
        non-text payloads are not bypassed.
        """
        if isinstance(user_input, str):
            result = self.input_sanitizer.sanitize(user_input)
            if self.debug and result.was_modified:
                logger.debug(f"[ChatOrchestrator] Input sanitized: {result.detected_patterns}")
            return result.sanitized, result.was_blocked

        if isinstance(user_input, list):
            blocked = False
            for part in user_input:
                if isinstance(part, dict):
                    for key in ("text", "content"):
                        val = part.get(key)
                        if isinstance(val, str):
                            r = self.input_sanitizer.sanitize(val)
                            if r.was_blocked:
                                blocked = True
                            else:
                                part[key] = r.sanitized
            return user_input, blocked

        return user_input, False

    def _clear_injected_hints(self, session, injected_hints: List[str]) -> None:
        """Remove transient hints injected during this turn from session history."""
        for hint in injected_hints:
            try:
                self.agent.context_engine.remove_hint(session.messages, hint)
            except Exception:
                pass

    def _has_open_tasks(self) -> bool:
        """Check if there are OPEN (not completed) tasks in the current task store."""
        if not hasattr(self.agent, '_task_manager') or not self.agent._task_manager:
            return False
        try:
            tasks = self.agent._task_manager.store.list_all()
            return any(t.status.value != "completed" for t in tasks)
        except Exception:
            return False

    def _count_completed_tasks(self) -> int:
        """Count completed tasks in the current task store."""
        if not hasattr(self.agent, '_task_manager') or not self.agent._task_manager:
            return 0
        try:
            return len(self.agent._task_manager.store.list_by_status("completed"))
        except Exception:
            return 0

    def _get_orphaned_tasks(self, session_id: str) -> List[str]:
        """Return IDs of tasks still in_progress at end of turn (potential orphans)."""
        if not hasattr(self.agent, '_task_manager') or not self.agent._task_manager:
            return []
        try:
            tasks = self.agent._task_manager.store.list_by_status("in_progress")
            return [t.id for t in tasks]
        except Exception:
            return []

    def _agent_summary_implies_completion(self, content: str) -> bool:
        """Heuristic: does the final answer imply the tracked work was finished?"""
        if not content:
            return False
        text = content.lower()
        done_signals = (
            "completed", "done", "finished", "all tasks", "successfully",
            "fixed", "implemented", "created", "verified", "all done",
        )
        return any(s in text for s in done_signals)

    async def run(
        self,
        user_input: Union[str, List[Dict[str, Any]]],
        session_id: str = "default",
        callbacks: Optional[Dict[str, Callable]] = None,
        stream: bool = False,
        streaming_funct: Optional[Callable] = None,
        generate_walkthrough: bool = False,
        emitter: Optional["StreamEmitter"] = None,
        **kwargs,
    ) -> str:
        """
        Main chat loop.

        Args:
            user_input: User message (string or multimodal content)
            session_id: Session identifier
            callbacks: Optional callback overrides
            stream: Enable streaming responses
            streaming_funct: Custom streaming function
            generate_walkthrough: Generate walkthrough summary

        Returns:
            Agent response as string
        """
        from logicore.providers.utils import extract_content

        # Sanitize user input for prompt injection protection.
        # Handles both plain strings and multimodal (list) content so that
        # injection fences smuggled inside image/part payloads are not skipped.
        user_input, blocked = self._sanitize_user_input(user_input)
        if blocked:
            logger.warning(f"[ChatOrchestrator] Blocked potentially malicious input")
            return "I cannot process this request. It contains patterns that may be attempting to manipulate my behavior."

        # Track transient hints so they can be removed from session history
        # at end of turn (prevents permanent context bloat across turns).
        injected_hints: List[str] = []

        # Merge callbacks
        active_callbacks = self.agent.callbacks.copy()
        if callbacks:
            active_callbacks.update(callbacks)
        if streaming_funct:
            active_callbacks["on_token"] = streaming_funct
            stream = True

        session = self.agent.get_session(session_id)

        # Loop detection / recovery state (graceful termination instead of a
        # silent "deadly" death when the model gets stuck after many tool calls).
        loop_engine = getattr(self.agent, "_loop_engine", None)
        tools_used_this_chat: List[str] = []
        last_tool_name: Optional[str] = None

        # Streaming event bus. ``emitter`` is None for the legacy
        # (callback-based) path; when set, we publish semantic events. Token /
        # reasoning / tool-call-chunk events are forwarded from the gateway via
        # ``on_event`` (see the LLM call below).
        on_event = emitter.emit if emitter else None
        if emitter:
            emitter.emit(StreamEvent.create(StreamEventType.RUN_START, {}))

        # Extract text for reminder routing and reasoning (needed early)
        text_for_reminder = user_input
        if isinstance(user_input, list):
            text_for_reminder, _ = extract_content(user_input)

        # Wire up reasoning controller (dynamically adjusts reasoning depth
        # based on the query - this is a prompt/context concern, not a routing
        # brain that decides plan vs act).
        if self.agent._reasoning_controller:
            try:
                query_text = text_for_reminder if isinstance(text_for_reminder, str) else str(text_for_reminder)
                self.agent._reasoning_controller.adjust_for_query(query_text)
                reasoning_addon = self.agent._reasoning_controller.get_system_prompt_addon()
                if reasoning_addon:
                    self.agent.context_engine.inject_hint(session.messages, reasoning_addon)
                    injected_hints.append(reasoning_addon)
            except Exception as e:
                if self.debug:
                    logger.warning(f"[ChatOrchestrator] ReasoningController adjustment failed: {e}")

        start_time = time.time()
        # Add messages to session — handle both single string and message list
        if isinstance(user_input, list):
            for msg in user_input:
                session.add_message(msg)
        else:
            session.add_message({"role": "user", "content": user_input})

        # Get tools
        all_tools = None
        tool_names: List[str] = []
        successful_tools_this_chat = 0
        if self.agent.supports_tools:
            all_tools = await self.agent.tool_executor.get_all_tools(
                self.agent.internal_tools, self.agent.disabled_tools,
                context_length=self._tool_context_window(),
                budget_config=self._tool_budget_config(),
            )
            tool_names = [
                t.get("function", {}).get("name", "")
                for t in all_tools
                if isinstance(t, dict)
            ]

        # Reminder routing hint
        reminder_hint = self.agent._build_reminder_routing_hint(text_for_reminder, tool_names)
        if reminder_hint:
            self.agent.context_engine.inject_hint(session.messages, reminder_hint)
            injected_hints.append(reminder_hint)

        # Local result cache for semantic dedup within this chat
        local_result_cache: Dict[str, Dict[str, Any]] = {}
        
        # Debug: Save messages (safe serialization)
        if self.debug:
            try:
                def _safe_serializer(obj):
                    """Handle non-JSON-serializable objects in messages."""
                    if hasattr(obj, 'to_dict'):
                        return obj.to_dict()
                    elif hasattr(obj, '__dict__'):
                        return obj.__dict__
                    return str(obj)
                
                
            except Exception as e:
                logger.debug(f"[ChatOrchestrator] Debug message save failed: {e}")
        
        for i in range(self.agent.max_iterations):
            if self.debug:
                logger.debug(f"\n[ChatOrchestrator] ITERATION {i+1}/{self.agent.max_iterations}")

            llm_start_time = time.time()

            if emitter:
                emitter.emit(StreamEvent.create(
                    StreamEventType.RUN_STEP,
                    {"iteration": i + 1, "max_iterations": self.agent.max_iterations},
                ))

            # Loop detection: mark turn start (drives stagnant-state detection).
            if loop_engine:
                await self._check_loop(AgentEventType.TURN_START, session_id)

            # Drop tools currently in loop cooldown so the model can't repeat them.
            if all_tools is not None:
                all_tools = self._filter_cooled_down_tools(all_tools, session_id)

            # 1. Get LLM response
            response = None
            try:
                llm_messages = session.messages
                _ctx_result, llm_messages = await self.agent.context_engine.prepare_messages(
                    llm_messages, session_id=session_id
                )

                on_token = active_callbacks.get("on_token")
                has_stream = hasattr(self.agent.gateway, 'chat_stream')
                use_stream = bool(has_stream and (stream or on_token is not None or emitter is not None))

                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.MESSAGE_START, {"iteration": i + 1}))

                if use_stream:
                    response = await self.agent.gateway.chat_stream(
                        llm_messages, tools=all_tools, on_token=on_token, on_event=on_event
                    )
                else:
                    response = await self.agent.gateway.chat(llm_messages, tools=all_tools)

            except Exception as e:
                response = await self._handle_llm_error(e, session, all_tools, session_id, generate_walkthrough, active_callbacks)
                if response is not None:
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.ERROR, {"message": str(e), "recoverable": False}))
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": response}))
                    self._clear_injected_hints(session, injected_hints)
                    return response
                continue

            if response is None:
                continue

            # 2. Parse response
            content, tool_calls, gemini_content = self._parse_response(response)

            # Empty response recovery
            if (not content or content.strip() == "") and not tool_calls and i > 0 and self.agent.execution_log:
                content, tool_calls = await self._handle_empty_response(
                    session, all_tools, session_id
                )

            # Record in session
            msg_dict = {"role": "assistant", "content": content}
            if tool_calls:
                msg_dict["tool_call_ids"] = [tc.get("id") for tc in tool_calls if isinstance(tc, dict)]
                msg_dict["tool_calls"] = tool_calls
            if gemini_content:
                msg_dict["gemini_content"] = gemini_content
            session.add_message(msg_dict)

            # Telemetry
            if self.agent.telemetry_enabled:
                self._record_telemetry(
                    session_id, llm_start_time, content, tool_calls, _ctx_result
                )

            # Loop detection: end-of-turn bookkeeping (stagnant detection), then
            # content repetition check. Either may trigger recovery instead of a
            # silent hard stop.
            if loop_engine:
                await self._check_loop(AgentEventType.TURN_END, session_id)
            if content:
                rec = await self._maybe_recover_loop(
                    await self._check_loop(AgentEventType.CONTENT, session_id, content=content[:4000]),
                    session, session_id, tools_used_this_chat, last_tool_name,
                )
                if rec and rec[0] == "terminate":
                    self._clear_injected_hints(session, injected_hints)
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": rec[1]}))
                    return rec[1]

            # 3. No tool calls = final response
            if not tool_calls:
                self._clear_injected_hints(session, injected_hints)
                final = await self._finalize_response(
                    content, session, session_id, active_callbacks,
                    generate_walkthrough, text_for_reminder, successful_tools_this_chat
                )
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": final}))
                return final

            # 4. Execute tools (with per-call loop detection)
            for tc in tool_calls:
                name, args, tc_id = self._extract_tool_call_details(tc)
                if not name:
                    continue

                # Loop detection: feed the tool call. Detects consecutive repeats
                # (e.g. hammering a wrong/non-existent tool like `open_file`).
                call_detected = await self._check_loop(
                    AgentEventType.TOOL_CALL, session_id,
                    tool_name=name, tool_args=args,
                )
                rec = await self._maybe_recover_loop(
                    call_detected, session, session_id,
                    tools_used_this_chat, last_tool_name,
                )
                if rec and rec[0] == "terminate":
                    self._clear_injected_hints(session, injected_hints)
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": rec[1]}))
                    return rec[1]

                # If recovery put this tool into cooldown, skip executing it now
                # and tell the model to use a different approach.
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
                    continue

                if self.debug:
                    args_preview = str(args)[:200] if args else "{}"
                    logger.debug(f"[Tool] Calling: {name}({args_preview})")

                if emitter:
                    emitter.emit(StreamEvent.create(
                        StreamEventType.TOOL_CALL_START,
                        {
                            "name": name,
                            "call_id": tc_id,
                            "args": args if isinstance(args, dict) else {},
                            "iteration": i + 1,
                        },
                    ))

                result = await self._execute_single_tool(
                    name, args, tc_id, session, session_id,
                    active_callbacks, local_result_cache
                )

                if self.debug:
                    success = result.get("success", True)
                    content_preview = str(result.get("content", ""))[:150]
                    status = "OK" if success else "FAILED"
                    logger.debug(f"[Tool] Result: {name} -> {status} | {content_preview}")

                if emitter:
                    preview = str(result.get("content", ""))[:280]
                    emitter.emit(StreamEvent.create(
                        StreamEventType.TOOL_CALL_END,
                        {
                            "name": name,
                            "call_id": tc_id,
                            "success": bool(result.get("success", True)),
                            "preview": preview,
                            "iteration": i + 1,
                        },
                    ))

                if result.get("success", True):
                    successful_tools_this_chat += 1
                    tools_used_this_chat.append(name)
                    last_tool_name = name

                # Loop detection: feed the tool result (failed repeats escalate
                # to stagnant-state detection and recovery).
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
                    self._clear_injected_hints(session, injected_hints)
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": rec[1]}))
                    return rec[1]

        self._clear_injected_hints(session, injected_hints)
        if emitter:
            emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": "Max iterations reached."}))
        return "Max iterations reached."

    def _parse_response(self, response) -> tuple[str, list, list]:
        """Parse NormalizedMessage into content, tool_calls, and provider-specific parts."""
        if isinstance(response, NormalizedMessage):
            gemini_content = response.extra.get("gemini_content") if response.extra else None
            return response.content, response.tool_calls, gemini_content
        content = getattr(response, 'content', str(response))
        tool_calls = getattr(response, 'tool_calls', [])
        return content, tool_calls, None

    async def _handle_llm_error(self, error, session, all_tools, session_id, generate_walkthrough, active_callbacks):
        """Handle LLM errors with retry logic."""
        error_str = str(error).lower()

        _TRANSIENT_PATTERNS = (
            "500", "502", "503", "504",
            "internal server error", "bad gateway",
            "service unavailable", "gateway timeout",
            "connection", "timeout", "timed out",
            "reset", "reset by peer", "broken pipe",
        )

        is_transient = any(p in error_str for p in _TRANSIENT_PATTERNS)

        if not is_transient:
            if self.debug:
                logger.error(f"[ChatOrchestrator] Terminal error: {error}")
            error_msg = f"Error during execution: {str(error)}"
            return error_msg

        # Retry with backoff
        _max_retries = 3
        _base_delay = 1.0

        for attempt in range(_max_retries):
            delay = _base_delay * (2 ** attempt)
            if self.debug:
                logger.warning(f"[ChatOrchestrator] Transient error (attempt {attempt + 1}/{_max_retries}). Retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)

            try:
                _ctx, _msgs = await self.agent.context_engine.prepare_messages(
                    session.messages, session_id=session_id
                )
                return await self.agent.gateway.chat(_msgs, tools=all_tools)
            except Exception as retry_err:
                if "does not support tools" in str(retry_err).lower():
                    break

        return None

    async def _handle_empty_response(self, session, all_tools, session_id):
        """Handle empty LLM responses with continuation prompt."""
        if self.debug:
            logger.debug(f"[ChatOrchestrator] Empty response. Injecting continuation prompt...")

        continuation_prompt = (
            "You were exploring the codebase and executing tools. Please continue your analysis "
            "or provide a summary of your findings so far. Do not leave the response empty."
        )
        session.add_message({"role": "user", "content": continuation_prompt})

        try:
            _ctx, _msgs = await self.agent.context_engine.prepare_messages(
                session.messages, session_id=session_id
            )
            continuation_response = await self.agent.gateway.chat(_msgs, tools=all_tools)
            c, tc, _ = self._parse_response(continuation_response)
            return c, tc
        except Exception as e:
            if self.debug:
                logger.warning(f"[ChatOrchestrator] Continuation failed: {e}")
            return "", []

    # === Loop detection / recovery helpers ===

    def _filter_cooled_down_tools(self, all_tools, session_id: str):
        """Drop tools currently in loop cooldown so the model can't call them."""
        engine = getattr(self.agent, "_loop_engine", None)
        if not engine:
            return all_tools
        cooled = set(engine.get_cooled_down_tools(session_id))
        if not cooled:
            return all_tools
        return [
            t for t in all_tools
            if (t.get("function") or {}).get("name") not in cooled
        ]

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
                logger.warning(f"[ChatOrchestrator] loop check failed: {e}")
            return None

    async def _maybe_recover_loop(self, result, session, session_id: str,
                                  tools_used, last_tool):
        """Turn a detected loop into a recovery action.

        Returns ``None`` when nothing was detected, ``("continue", None)`` when
        guidance was injected and the loop should keep going, or
        ``("terminate", message)`` when execution should stop gracefully.
        """
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
            session.add_message({"role": "user", "content": action.guidance_message})
        return ("continue", None)

    def _extract_tool_call_details(self, tc) -> tuple[str, dict, str]:
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

    async def _execute_single_tool(self, name, args, tc_id, session, session_id, callbacks, local_result_cache):
        """Execute a single tool call and return result."""
        # Parse arguments
        args, parse_error = self.agent.tool_executor.parse_tool_arguments(name, args)
        args = self.agent._normalize_tool_paths(session, name, args)

        if parse_error:
            result = self.agent.tool_executor.normalize_tool_result(name, {"success": False, "error": parse_error})
            if callbacks.get("on_tool_end"):
                await self._fire_callback(callbacks["on_tool_end"], session_id, name, result)

            tool_msg = {"role": "tool", "name": name, "content": result}
            if tc_id:
                tool_msg["tool_call_id"] = tc_id
            session.add_message(tool_msg)
            return result

        # Fire on_tool_start
        if callbacks.get("on_tool_start"):
            await self._fire_callback(callbacks["on_tool_start"], session_id, name, args)

        # Execute
        result = await self.agent.tool_executor.execute(
            name, args, session_id, local_result_cache
        )

        # Fire on_tool_end
        if callbacks.get("on_tool_end"):
            await self._fire_callback(callbacks["on_tool_end"], session_id, name, result)

        # Update directory context
        self.agent._update_tool_directory_context(session, name, args, result)

        # Add to session
        tool_msg = {
            "role": "tool",
            "name": name,
            "content": self.agent._serialize_tool_result_for_model(name, result)
        }
        if tc_id:
            tool_msg["tool_call_id"] = tc_id
        session.add_message(tool_msg)

        # Log
        is_error = not bool(result.get("success", True))
        if is_error:
            self.agent.execution_log.append(f"Tool {name} FAILED: {result.get('error', 'Unknown error')}")
        else:
            self.agent.execution_log.append(f"Tool {name} SUCCEEDED")

        return result

    async def _fire_callback(self, callback, *args):
        """Fire a callback (sync or async)."""
        if inspect.iscoroutinefunction(callback):
            await callback(*args)
        else:
            callback(*args)

    async def _finalize_response(self, content, session, session_id, active_callbacks, generate_walkthrough, text_for_reminder, successful_tools_this_chat):
        """Handle final response (no tool calls)."""
        # === AUTO-COMPLETION SAFETY NET ===
        # Passive cleanup, NOT a routing decision. If there are still tasks left
        # in_progress when the agent finishes and its summary clearly states the
        # work is done, auto-complete them so nothing is stuck forever. This does
        # not force planning - the LLM decides that via the system prompt.
        if self._has_open_tasks():
            orphaned = self._get_orphaned_tasks(session_id)
            if orphaned:
                if self._agent_summary_implies_completion(content):
                    for tid in orphaned:
                        try:
                            self.agent._task_manager.complete_task(tid)
                            if self.debug:
                                logger.debug(f"[ChatOrchestrator] Auto-completed orphaned task {tid}")
                        except Exception:
                            pass
                elif self.debug:
                    logger.warning(
                        f"[ChatOrchestrator] {len(orphaned)} task(s) still in_progress at finalize: {orphaned}"
                    )

        # Reminder verification
        if (
            self.agent._is_reminder_like_request(text_for_reminder)
            and successful_tools_this_chat == 0
            and self.agent._has_unverified_reminder_claim(content)
        ):
            content = (
                "I can't trigger a real timed reminder inside this chat unless an approved tool runs successfully. "
                "If you want, I can help set one up using an approved scheduler command or provide a local reminder script."
            )

        # Empty response synthesis
        if (not content or content.strip() == "") and self.agent.execution_log and len(self.agent.execution_log) > 3:
            content = await self._synthesize_findings(session, session_id)

        if not content or content.strip() == "":
            content = self.agent._generate_execution_summary()

        # Walkthrough
        if generate_walkthrough:
            walkthrough = await self.agent._generate_walkthrough_summary(session_id, active_callbacks)
            if walkthrough:
                content += f"\n\n---\n### Walkthrough Summary\n{walkthrough}"

        self.agent.execution_log.append(f"Task completed. Final response: {content[:200]}...")

        if active_callbacks.get("on_final_message"):
            active_callbacks["on_final_message"](session_id, content)

        return content

    async def _synthesize_findings(self, session, session_id):
        """Synthesize findings from execution log when LLM gives empty response."""
        try:
            synthesis_prompt = (
                "You have completed exploring the codebase. Based on your execution history below, "
                "provide a comprehensive summary of your findings.\n\n"
                f"Execution History:\n" + "\n".join(self.agent.execution_log[-30:])
            )
            session.add_message({"role": "user", "content": synthesis_prompt})
            _ctx, _msgs = await self.agent.context_engine.prepare_messages(
                session.messages, session_id=session_id
            )
            response = await self.agent.gateway.chat(_msgs, tools=None)
            content, _, _ = self._parse_response(response)
            return content
        except Exception as e:
            if self.debug:
                logger.error(f"[ChatOrchestrator] Synthesis failed: {e}")
            return self.agent._generate_execution_summary()

    def _record_telemetry(self, session_id, llm_start_time, content, tool_calls, ctx_result):
        """Record telemetry data."""
        try:
            from logicore.telemetry import TokenBreakdown

            llm_end_time = time.time()
            duration_ms = (llm_end_time - llm_start_time) * 1000

            # Token counting
            _system_tokens = self.agent.context_engine.token_estimator.count_tokens(
                " ".join(str(m.get("content", "")) for m in session.messages if m.get("role") == "system")
            )
            _tools_tokens = self.agent.context_engine.token_estimator.count_tokens(json.dumps([]))  # Placeholder

            breakdown = TokenBreakdown(
                system_instructions=_system_tokens,
                tool_definitions=_tools_tokens,
                messages=0,
                tool_results=0,
            )

            input_tokens = _system_tokens + _tools_tokens
            output_tokens = self.agent.context_engine.token_estimator.count_tokens(str(content or ""))
            provider_name = getattr(self.agent.provider, 'provider_name', 'unknown')

            self.agent.telemetry_tracker.record_request(
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=self.agent.model_name,
                provider=provider_name,
                duration_ms=duration_ms,
                token_breakdown=breakdown,
                tool_calls=len(tool_calls) if tool_calls else 0
            )
        except Exception as e:
            if self.debug:
                logger.error(f"[ChatOrchestrator] Telemetry error: {e}")
