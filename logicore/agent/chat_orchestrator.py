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
import re
import inspect
import asyncio
from typing import Dict, Any, Callable, List, Optional, Union
import logging

from logicore.gateway.gateway import NormalizedMessage
from logicore.security.input_sanitizer import InputSanitizer, InjectionAction
from logicore.stream.events import StreamEvent, StreamEventType
from logicore.stream.emitter import StreamEmitter
from logicore.runtime.loop_detection.engine import AgentEventType
from logicore.agent.tool_guardrails import (
    ToolCallGuardrailController,
    ToolCallGuardrailConfig,
    toolguard_synthetic_result,
    append_toolguard_guidance,
)
from logicore.agent.turn_retry_state import TurnRetryState, TransitionReason
from logicore.agent.feedback_handler import FeedbackHandler
from logicore.runtime.hooks.system import HookSystem
from logicore.runtime.hooks.types import HookPoint, HookAction, HookContext, HookResult
from logicore.agent.builtin_hooks import register_builtin_hooks
from logicore.agent.operational_memory import OperationalMemoryManager
from logicore.agent.progressive_compressor import ProgressiveCompressor, CompressionTrigger

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
        
        # Initialize tool guardrails controller
        guardrail_config = ToolCallGuardrailConfig()
        if hasattr(agent, "config") and hasattr(agent.config, "tool_loop_guardrails"):
            guardrail_config = agent.config.tool_loop_guardrails
        self.tool_guardrails = ToolCallGuardrailController(config=guardrail_config)
        self._tool_guardrail_halt_decision = None
        
        # Initialize turn retry state (one-shot recovery guards)
        self._turn_retry_state = TurnRetryState()

        # Consecutive same-response detection: if the LLM returns the same
        # text response N times in a row without calling tools, break the loop.
        self._last_response_hash: Optional[str] = None
        self._consecutive_same_response: int = 0
        self._max_consecutive_same_response: int = 3
        
        # Initialize feedback handler for user corrections
        self._feedback_handler = FeedbackHandler()
        
        # Initialize hook system (use agent's hook system if available, otherwise create new one)
        self._hook_system = getattr(agent, "_hook_system", None)
        if self._hook_system is None:
            self._hook_system = HookSystem()
            # Register built-in hooks only if we created a new hook system
            register_builtin_hooks(self._hook_system)
        
        # Initialize operational memory manager for failure pattern tracking
        self._operational_memory = OperationalMemoryManager(debug=debug)
        
        # Initialize progressive compressor for context management
        self._progressive_compressor = ProgressiveCompressor(debug=debug)
        
        # Track turn number for hooks
        self._turn_number = 0

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
            from logicore.runtime.context.token_estimator import get_model_context_window
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
        """Return IDs of tasks still in_progress OR pending at end of turn (potential orphans)."""
        if not hasattr(self.agent, '_task_manager') or not self.agent._task_manager:
            return []
        try:
            in_progress = self.agent._task_manager.store.list_by_status("in_progress")
            pending = self.agent._task_manager.store.list_by_status("pending")
            return [t.id for t in in_progress] + [t.id for t in pending]
        except Exception:
            return []

    def _agent_summary_implies_completion(self, content: str) -> bool:
        """Heuristic: does the final answer imply the tracked work was finished?

        Guards against negated claims (e.g. "I have NOT completed") which the
        previous naive substring match treated as completion.
        """
        if not content:
            return False
        text = content.lower()
        done_signals = (
            "completed", "done", "finished", "all tasks", "successfully",
            "fixed", "implemented", "created", "verified", "all done",
        )
        negation_markers = (
            "not ", "n't ", "never ", "no ", "haven't", "hasn't", "didn't",
            "won't", "isn't", "aren't", "without",
        )
        for signal in done_signals:
            idx = text.find(signal)
            if idx == -1:
                continue
            preceding = text[max(0, idx - 15):idx]
            if any(neg in preceding for neg in negation_markers):
                continue
            return True
        return False

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

        # Feedback detection: check for user corrections before processing
        feedback_result = None
        if isinstance(user_input, str) and user_input.strip():
            # M8: Pass LLM callable for semantic correction detection fallback
            llm_call = None
            if hasattr(self.agent, 'gateway') and self.agent.gateway:
                async def _feedback_llm_call(prompt: str) -> str:
                    msgs = [{"role": "user", "content": prompt}]
                    resp = await self.agent.gateway.send_chat(msgs)
                    return resp.get("content", "") if isinstance(resp, dict) else str(resp)
                llm_call = _feedback_llm_call
            feedback_result = await self._feedback_handler.handle_user_message(user_input, session, llm_call=llm_call)
            if feedback_result.has_injections:
                for injection in feedback_result.injected_hints:
                    self.agent.context_engine.inject_hint(session.messages, injection.message)
                    injected_hints.append(injection.message)
                if self.debug:
                    logger.debug(f"[FeedbackHandler] Detected {feedback_result.corrections_tracked} correction(s)")

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

        # Inject operational memory context (failure patterns, lessons learned)
        operational_context = self._operational_memory.format_for_system_prompt()
        if operational_context:
            self.agent.context_engine.inject_hint(session.messages, operational_context)
            injected_hints.append(operational_context)

        # Inject cumulative correction awareness so LLM adjusts behavior
        correction_prompt = self._feedback_handler.format_corrections_for_prompt(session)
        if correction_prompt:
            self.agent.context_engine.inject_hint(session.messages, correction_prompt)
            injected_hints.append(correction_prompt)

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
        
        # Reset tool guardrails and retry state once per run() call (one user turn)
        self.tool_guardrails.reset_for_turn()
        self._tool_guardrail_halt_decision = None
        self._turn_retry_state = TurnRetryState()

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

            # Increment turn number for hooks
            self._turn_number += 1

            # Reset session recovery state for this turn
            session.recovery_state.reset_for_turn()
            
            # Reset progressive compressor for this turn
            self._progressive_compressor.reset_for_new_turn(session_id)

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

                # Progressive compression: check if proactive compression needed
                if self._progressive_compressor.can_compress(session_id):
                    # Estimate token count using centralized estimator (tiktoken when available)
                    estimated_tokens = self.agent.context_engine.token_estimator.count_messages_tokens(llm_messages)
                    
                    # Get context window threshold
                    context_window = self._tool_context_window()
                    if context_window and estimated_tokens > context_window * 0.7:
                        # Compress if over 70% of context window
                        compressed_messages, boundary_msg = self._progressive_compressor.compress_messages(
                            llm_messages, session_id=session_id,
                            preserve_recent=10,
                            trigger=CompressionTrigger.PROACTIVE,
                        )
                        
                        if boundary_msg:
                            llm_messages = compressed_messages
                            # Inject boundary message into llm_messages (which the LLM will receive),
                            # not session.messages. Insert at position 0 so it appears as a system-level instruction.
                            llm_messages.insert(0, {"role": "system", "content": boundary_msg})
                            injected_hints.append(boundary_msg)
                            
                            if self.debug:
                                logger.debug(
                                    f"[ProgressiveCompressor] Proactive compression applied "
                                    f"(estimated {estimated_tokens} tokens -> ~{estimated_tokens // 2} tokens)"
                                )

                # Execute BEFORE_MODEL hooks
                before_model_ctx = HookContext(
                    hook_point=HookPoint.BEFORE_MODEL,
                    messages=llm_messages,
                    tools=all_tools or [],
                    session_id=session_id,
                    turn_number=self._turn_number,
                    metadata={"iteration": i + 1}
                )
                before_model_result = await self._hook_system.execute(
                    HookPoint.BEFORE_MODEL, before_model_ctx
                )
                
                # Handle hook results
                if before_model_result.action == HookAction.ABORT:
                    abort_msg = before_model_result.metadata.get("reason", "Hook aborted execution")
                    self._clear_injected_hints(session, injected_hints)
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": abort_msg}))
                    return abort_msg
                elif before_model_result.action == HookAction.SYNTHESIZE:
                    response = before_model_result.synthesized_response
                elif before_model_result.action == HookAction.MODIFY:
                    if before_model_result.modified_messages is not None:
                        llm_messages = before_model_result.modified_messages
                    if before_model_result.modified_tools is not None:
                        all_tools = before_model_result.modified_tools

                on_token = active_callbacks.get("on_token")
                has_stream = hasattr(self.agent.gateway, 'chat_stream')
                use_stream = bool(has_stream and (stream or on_token is not None or emitter is not None))

                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.MESSAGE_START, {"iteration": i + 1}))

                if response is None:
                    if use_stream:
                        response = await self.agent.gateway.chat_stream(
                            llm_messages, tools=all_tools, on_token=on_token, on_event=on_event
                        )
                    else:
                        response = await self.agent.gateway.chat(llm_messages, tools=all_tools)

            except Exception as e:
                response = await self._handle_llm_error(e, session, all_tools, session_id, generate_walkthrough, active_callbacks)
                if response is not None:
                    # If _handle_llm_error returned a string, it's a final error message — return it
                    # If it returned a NormalizedMessage (successful retry), continue to parse/execute
                    if isinstance(response, str):
                        if emitter:
                            emitter.emit(StreamEvent.create(StreamEventType.ERROR, {"message": str(e), "recoverable": False}))
                            emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": response}))
                        self._clear_injected_hints(session, injected_hints)
                        return response
                    # else: NormalizedMessage from successful retry — fall through to parsing below
                else:
                    continue

            if response is None:
                continue

            # 2. Parse response
            content, tool_calls, gemini_content = self._parse_response(response)

            # Execute AFTER_MODEL hooks
            after_model_ctx = HookContext(
                hook_point=HookPoint.AFTER_MODEL,
                messages=session.messages,
                tools=all_tools or [],
                model_response=response,
                tool_calls=tool_calls or [],
                session_id=session_id,
                turn_number=self._turn_number,
                metadata={"iteration": i + 1, "content": content}
            )
            after_model_result = await self._hook_system.execute(
                HookPoint.AFTER_MODEL, after_model_ctx
            )
            
            # Handle hook results
            if after_model_result.action == HookAction.ABORT:
                abort_msg = after_model_result.metadata.get("reason", "Hook aborted execution")
                self._clear_injected_hints(session, injected_hints)
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": abort_msg}))
                return abort_msg
            elif after_model_result.action == HookAction.MODIFY:
                if after_model_result.modified_tool_calls is not None:
                    tool_calls = after_model_result.modified_tool_calls
                if after_model_result.modified_messages is not None:
                    session.messages = after_model_result.modified_messages

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

            # Telemetry — always record (DB persistence), visibility gated by telemetry_enabled
            self._record_telemetry(
                session_id, llm_start_time, response, tool_calls, emitter
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

            # Simple same-response detection: if the LLM returns identical
            # content N times in a row (without tool calls), break the loop.
            # This catches cases the chunk-based detector misses (e.g.
            # structured responses with lists/headings that get skipped).
            if content and not tool_calls:
                import hashlib
                resp_hash = hashlib.md5(content.strip().encode()).hexdigest()
                if resp_hash == self._last_response_hash:
                    self._consecutive_same_response += 1
                else:
                    self._consecutive_same_response = 0
                self._last_response_hash = resp_hash

                if self._consecutive_same_response >= self._max_consecutive_same_response:
                    logger.warning(
                        f"[ChatOrchestrator] Same response repeated "
                        f"{self._consecutive_same_response} times — breaking loop"
                    )
                    stop_msg = (
                        "I notice I keep repeating the same response. "
                        "Let me stop here. Please let me know if you have a "
                        "specific question or task I can help with."
                    )
                    self._clear_injected_hints(session, injected_hints)
                    session.messages.append({"role": "assistant", "content": stop_msg})
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": stop_msg}))
                    return stop_msg

            # 3. No tool calls = final response
            if not tool_calls:
                self._clear_injected_hints(session, injected_hints)
                
                # M9: Hallucination check — compare claims vs execution reality
                hallucination_warning = self._check_response_hallucination(
                    content, self.agent.execution_log, successful_tools_this_chat
                )
                if hallucination_warning:
                    session.messages.append({"role": "system", "content": hallucination_warning})
                    # Re-run one more iteration so the LLM can correct itself
                    continue
                
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

                # Tool guardrails: pre-execution check
                guardrail_decision = self.tool_guardrails.before_call(name, args)
                if not guardrail_decision.allows_execution:
                    # Tool should be blocked — inject synthetic result
                    result = self.agent.tool_executor.normalize_tool_result(
                        name,
                        {"success": False, "error": guardrail_decision.message}
                    )
                    # Add classification metadata for downstream consumers
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
                    
                    # Record halt decision
                    if guardrail_decision.should_halt:
                        self._tool_guardrail_halt_decision = guardrail_decision
                    
                    continue

                # Execute BEFORE_TOOL_EXECUTION hooks
                before_tool_ctx = HookContext(
                    hook_point=HookPoint.BEFORE_TOOL_EXECUTION,
                    messages=session.messages,
                    tools=all_tools or [],
                    tool_name=name,
                    tool_args=args,
                    session_id=session_id,
                    turn_number=self._turn_number,
                    metadata={"iteration": i + 1, "call_id": tc_id}
                )
                before_tool_result = await self._hook_system.execute(
                    HookPoint.BEFORE_TOOL_EXECUTION, before_tool_ctx
                )
                
                # Handle hook results
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
                    continue
                elif before_tool_result.action == HookAction.ABORT:
                    abort_msg = before_tool_result.metadata.get("reason", "Hook aborted tool execution")
                    self._clear_injected_hints(session, injected_hints)
                    if emitter:
                        emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": abort_msg}))
                    return abort_msg
                elif before_tool_result.action == HookAction.MODIFY:
                    if before_tool_result.modified_tool_args is not None:
                        args = before_tool_result.modified_tool_args

                result = await self._execute_single_tool(
                    name, args, tc_id, session, session_id,
                    active_callbacks, local_result_cache
                )

                # Execute AFTER_TOOL_EXECUTION hooks
                after_tool_ctx = HookContext(
                    hook_point=HookPoint.AFTER_TOOL_EXECUTION,
                    messages=session.messages,
                    tools=all_tools or [],
                    tool_name=name,
                    tool_args=args,
                    tool_result=result,
                    session_id=session_id,
                    turn_number=self._turn_number,
                    metadata={"iteration": i + 1, "call_id": tc_id}
                )
                after_tool_result = await self._hook_system.execute(
                    HookPoint.AFTER_TOOL_EXECUTION, after_tool_ctx
                )
                
                # Handle hook results
                if after_tool_result.action == HookAction.MODIFY:
                    if after_tool_result.tool_result is not None:
                        result = after_tool_result.tool_result

                # Tool guardrails: post-execution observation
                is_error = not bool(result.get("success", True))
                guardrail_decision = self.tool_guardrails.after_call(
                    name, args, str(result.get("content") or result.get("error") or ""),
                    failed=is_error,
                )
                
                # Apply guardrail guidance to result
                if guardrail_decision.action in {"warn", "halt"}:
                    result_str = self.agent._serialize_tool_result_for_model(name, result)
                    result_str = append_toolguard_guidance(result_str, guardrail_decision)
                    # Update the result content with guidance
                    if isinstance(result, dict):
                        result["_guardrail_guidance"] = guardrail_decision.message
                
                # Record halt decision
                if guardrail_decision.should_halt:
                    self._tool_guardrail_halt_decision = guardrail_decision

                # Operational memory: record failure patterns and check escalation
                if is_error:
                    error_msg = str(result.get('error', ''))
                    _cls = result.get("_classification", {})
                    error_type = _cls.get("error_category", "unknown") if _cls else "unknown"
                    _recovery = _cls.get("recovery_action") if _cls else None
                    
                    # Record failure in operational memory
                    pattern = self._operational_memory.record_tool_failure(
                        tool_name=name,
                        error_type=error_type,
                        error_message=error_msg,
                        recovery_action=_recovery,
                    )
                    
                    # Record recovery attempt for escalation tracking
                    self._operational_memory._session_state.record_recovery_attempt(
                        error_type=error_type,
                        tool_name=name,
                    )
                    
                    # Check if recovery should be escalated
                    if self._operational_memory.should_escalate_recovery(
                        error_type=error_type,
                        tool_name=name,
                        max_attempts=3,
                    ):
                        # Inject escalation message
                        escalation_msg = (
                            f"Recovery for {error_type} on '{name}' has failed multiple times. "
                            f"Consider a fundamentally different approach instead of retrying."
                        )
                        self.agent.context_engine.inject_hint(session.messages, escalation_msg)
                        injected_hints.append(escalation_msg)
                        
                        if self.debug:
                            logger.debug(f"[OperationalMemory] Escalating recovery for {pattern.pattern_id}")
                else:
                    # Record success after failure
                    if name in [p.tool_name for p in self._operational_memory._session_state.failure_patterns.values()]:
                        self._operational_memory.record_tool_success(
                            tool_name=name,
                            error_type="previous_failure",
                        )

                # Feedback handling: inject hints for tool failures
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

                # Track previous agent action for context
                self._feedback_handler.set_previous_action(
                    f"Called tool '{name}' with args: {str(args)[:100]}"
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

                # Track tool result in session for pattern detection
                args_hash = str(hash(json.dumps(args, sort_keys=True, default=str)))[:16] if args else None
                session.add_tool_result(
                    tool_name=name,
                    success=bool(result.get("success", True)),
                    result_summary=str(result.get("content") or result.get("error") or "")[:200],
                    args_hash=args_hash
                )

                # Record transition for auditable recovery tracking
                if is_error:
                    self._turn_retry_state.record_transition(
                        TransitionReason.TOOL_USE,
                        detail=f"Tool '{name}' failed: {str(result.get('error', ''))[:100]}"
                    )

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

            # Tool guardrails: check for halt after tool execution
            if self._tool_guardrail_halt_decision is not None:
                decision = self._tool_guardrail_halt_decision
                halt_response = (
                    f"I stopped retrying {decision.tool_name} because it hit the "
                    f"tool-call guardrail ({decision.code}) after {decision.count} "
                    f"repeated non-progressing attempts. The last tool result "
                    f"explains the blocker; the next step is to change strategy "
                    f"instead of repeating the same call."
                )
                self._clear_injected_hints(session, injected_hints)
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": halt_response}))
                return halt_response

            # Execute AFTER_TURN hooks (end of iteration)
            # Build task summary for verification hooks
            task_summary = {}
            if hasattr(self.agent, '_task_manager') and self.agent._task_manager:
                try:
                    task_summary = self.agent._task_manager.get_task_summary()
                except Exception:
                    pass
            
            after_turn_ctx = HookContext(
                hook_point=HookPoint.AFTER_TURN,
                messages=session.messages,
                tools=all_tools or [],
                session_id=session_id,
                turn_number=self._turn_number,
                metadata={
                    "iteration": i + 1,
                    "successful_tools": successful_tools_this_chat,
                    "tools_used": tools_used_this_chat,
                    "task_summary": task_summary,
                }
            )
            after_turn_result = await self._hook_system.execute(
                HookPoint.AFTER_TURN, after_turn_ctx
            )
            
            # Handle hook results
            if after_turn_result.action == HookAction.ABORT:
                abort_msg = after_turn_result.metadata.get("reason", "Hook aborted turn")
                self._clear_injected_hints(session, injected_hints)
                if emitter:
                    emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": abort_msg}))
                return abort_msg
            elif after_turn_result.action == HookAction.MODIFY:
                if after_turn_result.modified_messages is not None:
                    session.messages = after_turn_result.modified_messages

            # Wire memory extraction hook — extract cross-session learnings
            if after_turn_result.metadata.get("should_extract_memories"):
                self._extract_session_memories(session, user_input)

            # Operational memory: extract lessons from failure patterns
            extracted_lessons = self._extract_operational_lessons()
            if extracted_lessons:
                lessons_text = "## Lessons from This Session\n" + "\n".join(f"- {l}" for l in extracted_lessons)
                self.agent.context_engine.inject_hint(session.messages, lessons_text)
                injected_hints.append(lessons_text)

        self._clear_injected_hints(session, injected_hints)
        if emitter:
            emitter.emit(StreamEvent.create(StreamEventType.DONE, {"content": "Max iterations reached."}))
        return "Max iterations reached."

    def _extract_operational_lessons(self) -> List[str]:
        """Extract lessons from failure patterns for future reference.
        
        This method analyzes failure patterns and extracts actionable lessons
        that can be applied in future sessions. Returns list of lesson texts
        for injection into current session.
        """
        patterns = self._operational_memory._session_state.failure_patterns
        extracted = []
        
        for pattern_id, pattern in patterns.items():
            # Extract lesson if failure rate is high
            if pattern.failure_count >= 3 and pattern.failure_rate > 0.7:
                lesson_text = (
                    f"Tool '{pattern.tool_name}' consistently fails with "
                    f"{pattern.error_type} errors. Consider alternative approaches."
                )
                
                self._operational_memory.extract_lesson(
                    trigger=pattern.error_type,
                    lesson=lesson_text,
                    confidence=pattern.failure_rate,
                    examples=[pattern.error_message[:100]],
                )
                extracted.append(lesson_text)
            
            # Extract lesson for specific error patterns
            if "not found" in pattern.error_message.lower():
                lesson_text = (
                    f"File/directory not found when using '{pattern.tool_name}'. "
                    f"Always verify paths exist before operations."
                )
                
                self._operational_memory.extract_lesson(
                    trigger="not_found_error",
                    lesson=lesson_text,
                    confidence=0.8,
                )
                extracted.append(lesson_text)
            
            elif "permission denied" in pattern.error_message.lower():
                lesson_text = (
                    f"Permission denied when using '{pattern.tool_name}'. "
                    f"Check file permissions or use elevated privileges."
                )
                
                self._operational_memory.extract_lesson(
                    trigger="permission_denied",
                    lesson=lesson_text,
                    confidence=0.8,
                )
                extracted.append(lesson_text)

        return extracted

    def _extract_session_memories(self, session, user_input):
        """Extract cross-session learnings from the current session.
        
        Captures user corrections, preferences, and successful approaches
        for persistence across sessions.
        """
        corrections = getattr(session, 'corrections_made', [])
        if not corrections:
            return
        
        for correction in corrections[-5:]:  # Last 5 corrections
            ctype = correction.get('type', 'other')
            corrected = correction.get('corrected', '')
            if corrected:
                self._operational_memory.extract_lesson(
                    trigger=f"user_correction_{ctype}",
                    lesson=f"User corrected {ctype}: {corrected[:200]}",
                    confidence=correction.get('confidence', 0.7),
                )

    def _parse_response(self, response) -> tuple[str, list, list]:
        """Parse NormalizedMessage into content, tool_calls, and provider-specific parts."""
        if isinstance(response, NormalizedMessage):
            gemini_content = response.extra.get("gemini_content") if response.extra else None
            return response.content, response.tool_calls, gemini_content
        content = getattr(response, 'content', str(response))
        tool_calls = getattr(response, 'tool_calls', [])
        return content, tool_calls, None

    async def _handle_llm_error(self, error, session, all_tools, session_id, generate_walkthrough, active_callbacks):
        """Handle LLM errors with structured classification and retry logic.
        
        Uses the error classifier to determine the right recovery strategy
        for each error type, mirroring the hermes-agent's structured approach.
        """
        from logicore.tools.error_classifier import classify_tool_error, RecoveryAction
        
        classified = classify_tool_error(error, tool_name="llm_gateway")
        
        if self.debug:
            logger.warning(
                f"[ChatOrchestrator] LLM error classified: {classified.reason.value} "
                f"(recovery={classified.recovery_action.value}, retryable={classified.retryable})"
            )
        
        # Non-retryable errors: surface immediately
        if not classified.retryable or classified.recovery_action == RecoveryAction.ABORT_WITH_MESSAGE:
            error_msg = f"Error during execution: {str(error)}"
            return error_msg
        
        # Compress context errors: try to compress and retry (once per turn)
        if classified.recovery_action == RecoveryAction.COMPRESS_CONTEXT:
            if self._turn_retry_state.context_compression_attempted:
                if self.debug:
                    logger.warning("[ChatOrchestrator] Context compression already attempted this turn, aborting.")
                return None
            
            # Check if progressive compressor can compress
            if not self._progressive_compressor.can_compress(session_id):
                if self.debug:
                    logger.warning("[ChatOrchestrator] Progressive compressor blocked (circuit breaker).")
                return None
            
            self._turn_retry_state.mark_context_compression()
            self._turn_retry_state.record_transition(
                TransitionReason.CONTEXT_COMPRESSED,
                detail="Context too large, attempting reactive compression"
            )
            
            if self.debug:
                logger.warning("[ChatOrchestrator] Context too large, attempting reactive compression...")
            
            try:
                # Apply reactive compression
                compressed_messages, boundary_msg = self._progressive_compressor.compress_messages(
                    session.messages, session_id=session_id,
                    preserve_recent=10,
                    trigger=CompressionTrigger.REACTIVE,
                )
                
                if boundary_msg:
                    session.messages = compressed_messages
                    # Inject boundary message
                    self.agent.context_engine.inject_hint(session.messages, boundary_msg)
                    
                    if self.debug:
                        logger.debug("[ProgressiveCompressor] Reactive compression applied")
                
                # Retry with compressed messages
                _ctx, _msgs = await self.agent.context_engine.prepare_messages(
                    session.messages, session_id=session_id
                )
                return await self.agent.gateway.chat(_msgs, tools=all_tools)
            except Exception as compress_err:
                if self.debug:
                    logger.warning(f"[ChatOrchestrator] Reactive compression retry failed: {compress_err}")
                return None
        
        # Rate limit errors: check if we've already retried (once per turn)
        if classified.recovery_action == RecoveryAction.RETRY_SAME:
            if self._turn_retry_state.rate_limit_retry_attempted:
                if self.debug:
                    logger.warning("[ChatOrchestrator] Rate limit retry already attempted this turn, aborting.")
                return None
            
            self._turn_retry_state.mark_rate_limit_retry()
            self._turn_retry_state.record_transition(
                TransitionReason.RATE_LIMITED,
                detail=f"Rate limited, retrying with backoff"
            )
        
        # Transient errors: retry with exponential backoff (respecting retry budget)
        _base_delay = 1.0

        while self._turn_retry_state.can_retry():
            attempt = self._turn_retry_state.retry_count
            delay = _base_delay * (2 ** attempt)
            
            if self.debug:
                logger.warning(
                    f"[ChatOrchestrator] Transient error (attempt {attempt + 1}/{self._turn_retry_state.max_retries}). "
                    f"Retrying in {delay:.1f}s..."
                )
            
            await asyncio.sleep(delay)
            self._turn_retry_state.increment_retry()
            
            try:
                _ctx, _msgs = await self.agent.context_engine.prepare_messages(
                    session.messages, session_id=session_id
                )
                return await self.agent.gateway.chat(_msgs, tools=all_tools)
            except Exception as retry_err:
                # Check if the retry error is different (e.g., escalation to terminal)
                retry_classified = classify_tool_error(retry_err, tool_name="llm_gateway")
                if not retry_classified.retryable:
                    self._turn_retry_state.record_transition(
                        TransitionReason.INITIAL,
                        detail=f"Retry failed with non-retryable error: {retry_err}"
                    )
                    break
                if "does not support tools" in str(retry_err).lower():
                    self._turn_retry_state.record_transition(
                        TransitionReason.INITIAL,
                        detail=f"Retry failed: model does not support tools"
                    )
                    break

        return None

    async def _handle_empty_response(self, session, all_tools, session_id):
        """Handle empty LLM responses with continuation prompt."""
        if self.debug:
            logger.debug(f"[ChatOrchestrator] Empty response. Injecting continuation prompt...")

        # Build context-aware continuation: include last tool result if available
        last_tool_result = ""
        if self.agent.execution_log:
            last_entry = self.agent.execution_log[-1]
            last_tool_result = f"\n\nLast tool result: {last_entry}"

        continuation_prompt = (
            "The previous response was empty. You MUST take exactly one action:\n"
            "1. If the user's task is incomplete, call the next appropriate tool to continue.\n"
            "2. If all tool work is done, provide a brief summary of what was accomplished.\n"
            f"{last_tool_result}\n\n"
            "Do NOT describe what you would do — actually do it with a tool call, or provide the final summary."
        )
        session.add_message({"role": "system", "content": continuation_prompt})

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
            session.add_message({"role": "system", "content": action.guidance_message})
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

            tool_msg = {"role": "tool", "name": name, "content": self.agent._serialize_tool_result_for_model(name, result)}
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

        is_error = not bool(result.get("success", True))

        # Inject recovery signals based on error classification.
        # When a tool fails with a specific recovery action, inject a message
        # that helps the LLM understand what went wrong and how to self-correct.
        if is_error and "_classification" in result:
            classification = result["_classification"]
            recovery_action = classification.get("recovery_action", "")
            
            if recovery_action == "inject_signal":
                # Tool/validation errors: inject a signal telling the LLM to change approach
                error_msg = classification.get("error", "")
                signal = (
                    f"The tool '{name}' failed with a deterministic error. "
                    f"Error: {error_msg}. "
                    f"Try a different approach — do NOT retry the same call."
                )
                session.add_message({"role": "system", "content": signal})
            
            elif recovery_action == "compress_context":
                # Context/payload too large: signal that context needs compression
                signal = (
                    f"The tool '{name}' failed because the request was too large. "
                    f"Consider breaking the request into smaller pieces or "
                    f"reducing the amount of data being processed."
                )
                session.add_message({"role": "system", "content": signal})

        # Log
        if is_error:
            self.agent.execution_log.append(f"Tool {name} FAILED: {result.get('error', 'Unknown error')}")
        else:
            self.agent.execution_log.append(f"Tool {name} SUCCEEDED")

        # After load_skill, dynamically register the skill's tools on the agent
        if name == "load_skill" and not is_error:
            skill_name = args.get("skill_name")
            if skill_name and hasattr(self.agent, '_skill_index_entries'):
                if skill_name in self.agent._skill_index_entries:
                    skills_dir, entry = self.agent._skill_index_entries[skill_name]
                    from logicore.skills.loader import SkillLoader
                    skill = SkillLoader.load_skill_by_index(skills_dir, skill_name)
                    if skill:
                        self.agent._register_skill_tools(skill)
                        # Rebuild system prompt so new tools are available to the model
                        self.agent._rebuild_system_prompt_with_tools()

        return result

    async def _fire_callback(self, callback, *args):
        """Fire a callback (sync or async)."""
        if inspect.iscoroutinefunction(callback):
            await callback(*args)
        else:
            callback(*args)

    # =========================================================================
    # M9: HALLUCINATION CHECK — dynamic, not rule-based
    # =========================================================================
    _COMPLETION_CLAIMS = re.compile(
        r"\b(completed?|fixed?|resolved?|created?|updated?|deleted?|installed?|configured?|set up|deployed?|migrated?|done|success|finished|saved|written|applied)\b",
        re.IGNORECASE,
    )
    # Matches actual execution log format: "Tool X SUCCEEDED" / "Tool X FAILED: ..."
    _TOOL_SUCCEEDED_RE = re.compile(r"^Tool (\S+) SUCCEEDED$")
    _TOOL_FAILED_RE = re.compile(r"^Tool (\S+) FAILED:")

    def _check_response_hallucination(
        self, response: str, execution_log: list, successful_tools_this_chat: int
    ) -> Optional[str]:
        """Compare response claims against actual tool execution results.

        Returns a warning string if the response claims success but tools
        actually failed, or claims work was done but no tools were called.
        This is *dynamic* — it checks real execution history, not static rules.
        """
        if not response or not response.strip():
            return None

        claims = self._COMPLETION_CLAIMS.findall(response)
        if not claims:
            return None

        # Collect tool execution outcomes from the log
        tool_outcomes: Dict[str, str] = {}
        for entry in execution_log:
            if not isinstance(entry, str):
                continue
            # Match actual log format: "Tool X SUCCEEDED" / "Tool X FAILED: ..."
            m_suc = self._TOOL_SUCCEEDED_RE.match(entry)
            m_fail = self._TOOL_FAILED_RE.match(entry)
            if m_suc:
                tool_outcomes[m_suc.group(1)] = "succeeded"
            elif m_fail:
                tool_outcomes[m_fail.group(1)] = "failed"

        failed_tools = [t for t, s in tool_outcomes.items() if s.upper() in ("ERROR", "FAILED")]
        succeeded_tools = [t for t, s in tool_outcomes.items() if s.upper() in ("COMPLETED", "SUCCEEDED")]

        # Case 1: Response claims success but ALL tools failed
        if failed_tools and not succeeded_tools and successful_tools_this_chat == 0:
            return (
                f"Your response claims work was done, but every tool call "
                f"failed ({', '.join(failed_tools[:3])}). "
                f"Either retry with a different approach or explain what went wrong."
            )

        # Case 2: Response claims specific tool success but that tool actually failed
        mentioned_tool_claims = re.findall(
            r"(?:used|called|executed|ran)\s+(\w+)", response, re.IGNORECASE
        )
        for tool_name in mentioned_tool_claims:
            if tool_name.lower() in {t.lower() for t in failed_tools}:
                return (
                    f"Your response mentions successfully using '{tool_name}', "
                    f"but that tool actually failed. Correct your response or retry."
                )

        # Case 3: No tools called but response claims completion
        if successful_tools_this_chat == 0 and len(claims) >= 2:
            return (
                "You are claiming multiple things were completed, but no tools "
                "were executed this turn. Either run the tools or remove the claims."
            )

        return None

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

    def _record_telemetry(self, session_id, llm_start_time, response, tool_calls, emitter):
        """Record telemetry from a single LLM call.

        Always accumulates in memory. DB persistence only if session row exists
        (final _persist_session handles the definitive write).
        """
        try:
            from logicore.telemetry.canonical import normalize_usage
            from logicore.telemetry.pricing import estimate_usage_cost

            llm_end_time = time.time()
            duration_ms = (llm_end_time - llm_start_time) * 1000

            raw_usage = getattr(response, "usage", None)
            provider_name = getattr(self.agent.provider, "provider_name", "unknown")

            canonical = normalize_usage(raw_usage, provider=provider_name)

            # Auto-prefix-cache estimation for Ollama and Gemini:
            # Both providers do transparent prefix caching.
            #
            # Ollama: prompt_tokens INCREASES between turns (reports total
            # prompt including cached prefix). Estimated cache = previous turn.
            #
            # Gemini: prompt_tokens DECREASES between turns (reports only
            # uncached portion). Estimated cache = previous - current.
            if provider_name in ("ollama", "gemini") and raw_usage:
                prompt_tokens = 0
                if isinstance(raw_usage, dict):
                    prompt_tokens = raw_usage.get("prompt_tokens", 0)
                else:
                    prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0

                if prompt_tokens > 0:
                    prev_key = f"_prefix_cache_prev_{session_id}"
                    prev_prompt = getattr(self.agent, "_prefix_cache_tracker", {}).get(prev_key, 0)

                    if prev_prompt > 0:
                        from logicore.telemetry.canonical import CanonicalUsage
                        if provider_name == "ollama" and prompt_tokens > prev_prompt:
                            # Ollama: total grew → new = current - prev, cached = prev
                            canonical = CanonicalUsage(
                                input_tokens=prompt_tokens - prev_prompt,
                                output_tokens=canonical.output_tokens,
                                cache_read_tokens=prev_prompt,
                                cache_write_tokens=canonical.cache_write_tokens,
                                reasoning_tokens=canonical.reasoning_tokens,
                            )
                        elif provider_name == "gemini" and prompt_tokens < prev_prompt:
                            # Gemini: total shrank → only uncached portion shown
                            canonical = CanonicalUsage(
                                input_tokens=prompt_tokens,
                                output_tokens=canonical.output_tokens,
                                cache_read_tokens=prev_prompt - prompt_tokens,
                                cache_write_tokens=canonical.cache_write_tokens,
                                reasoning_tokens=canonical.reasoning_tokens,
                            )

                    # Store for next turn
                    if not hasattr(self.agent, "_prefix_cache_tracker"):
                        self.agent._prefix_cache_tracker = {}
                    self.agent._prefix_cache_tracker[prev_key] = prompt_tokens

            self.agent.session_input_tokens += canonical.input_tokens
            self.agent.session_output_tokens += canonical.output_tokens
            self.agent.session_cache_read_tokens += canonical.cache_read_tokens
            self.agent.session_cache_write_tokens += canonical.cache_write_tokens
            self.agent.session_reasoning_tokens += canonical.reasoning_tokens
            self.agent.session_api_calls += 1

            base_url = getattr(self.agent.provider, "base_url", None)
            api_key = getattr(self.agent.provider, "api_key", None)
            cost = estimate_usage_cost(
                self.agent.model_name, canonical,
                provider=provider_name, base_url=base_url, api_key=api_key,
            )
            self.agent.session_estimated_cost_usd += float(cost.amount_usd or 0)
            self.agent.session_cost_status = cost.status
            self.agent.session_cost_source = cost.source

            if self.agent._storage and self.agent._storage.initialized:
                if self.agent._storage.session_exists(session_id):
                    self.agent._storage.save_telemetry(
                        session_id,
                        input_tokens=canonical.input_tokens,
                        output_tokens=canonical.output_tokens,
                        cache_read_tokens=canonical.cache_read_tokens,
                        cache_write_tokens=canonical.cache_write_tokens,
                        reasoning_tokens=canonical.reasoning_tokens,
                        tool_calls=len(tool_calls) if tool_calls else 0,
                        api_calls=1,
                        estimated_cost_usd=float(cost.amount_usd or 0),
                        cost_status=cost.status,
                    )

            if self.agent.telemetry_enabled or self.debug:
                logger.info(
                    f"[Telemetry] in={canonical.input_tokens} out={canonical.output_tokens} "
                    f"cache_r={canonical.cache_read_tokens} cache_w={canonical.cache_write_tokens} "
                    f"reasoning={canonical.reasoning_tokens} total={canonical.total_tokens} "
                    f"cost={cost.label} session_total_in={self.agent.session_input_tokens} "
                    f"session_total_out={self.agent.session_output_tokens} "
                    f"session_api_calls={self.agent.session_api_calls}"
                )

            if self.agent.telemetry_enabled and hasattr(self.agent, "telemetry_tracker") and self.agent.telemetry_tracker:
                self.agent.telemetry_tracker.record_request(
                    session_id=session_id,
                    input_tokens=canonical.input_tokens,
                    output_tokens=canonical.output_tokens,
                    model=self.agent.model_name,
                    provider=provider_name,
                    duration_ms=duration_ms,
                    tool_calls=len(tool_calls) if tool_calls else 0,
                )

            if self.agent.telemetry_enabled and emitter:
                try:
                    from logicore.stream.events import StreamEvent, StreamEventType
                    emitter.emit(StreamEvent.create(StreamEventType.USAGE, {
                        "input_tokens": canonical.input_tokens,
                        "output_tokens": canonical.output_tokens,
                        "cache_read_tokens": canonical.cache_read_tokens,
                        "cache_write_tokens": canonical.cache_write_tokens,
                        "reasoning_tokens": canonical.reasoning_tokens,
                        "total_tokens": canonical.total_tokens,
                        "api_calls": self.agent.session_api_calls,
                        "estimated_cost_usd": self.agent.session_estimated_cost_usd,
                        "cost_status": cost.status,
                        "session_id": session_id,
                    }))
                except Exception:
                    pass

        except Exception as e:
            if self.debug:
                logger.error(f"[ChatOrchestrator] Telemetry error: {e}")
