"""Full-fidelity agent session tracer.

Records every layer of a SmartAgent run to a JSONL file so a session can be
replayed and debugged offline:

    session_start   agent config, system prompt, tool list
    turn_start      user input + session ids alive before the turn
    llm_request     exact message array handed to the provider (after
                    masking / compression / truncation) + token estimate
    model_response  raw model output: content, tool_calls, usage
    thinking        reasoning deltas streamed per iteration
    tool_call       tool name, args, full result
    usage / error   stream level usage + failures
    turn_end        final answer + full session transcript (incl. walkthrough)

Usage:

    tracer = SessionTracer(agent)
    tracer.begin_turn(user_input)
    run = await agent.stream_run(user_input)
    async for ev in run.stream_events():
        tracer.trace_event(ev)
        ...
    final = await run
    tracer.end_turn(final)

Set LOGICORE_TRACE=0 to disable, LOGICORE_TRACE_DIR to change the output dir,
LOGICORE_TRACE_MAX_FIELD to change the per-string truncation limit.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from logicore.agent.builtin_hooks import register_builtin_hooks
from logicore.runtime.hooks.system import HookSystem
from logicore.runtime.hooks.types import HookPoint, HookResult
from logicore.stream.events import StreamEventType

DEFAULT_MAX_FIELD = int(os.environ.get("LOGICORE_TRACE_MAX_FIELD", "60000"))


def _clip(value: Any, limit: int = DEFAULT_MAX_FIELD) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n...[trace truncated {len(value) - limit} chars]"
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return value
    return text[:limit] + f"\n...[trace truncated {len(text) - limit} chars]"


def _safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


class SessionTracer:
    def __init__(self, agent: Any = None, path: Optional[str] = None,
                 enabled: Optional[bool] = None):
        if enabled is None:
            enabled = os.environ.get("LOGICORE_TRACE", "1") != "0"
        self.enabled = bool(enabled)
        self.agent = agent
        self.path: Optional[str] = None
        self._file = None
        self._lock = threading.Lock()
        self._seq = 0
        self._turn = 0
        self._iteration = 0
        self._session_id: Optional[str] = None
        self._thinking: List[str] = []
        self._tokens: List[str] = []
        if not self.enabled:
            return
        if path is None:
            root = os.environ.get("LOGICORE_TRACE_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "debug", "traces",
            )
            os.makedirs(root, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(root, f"session_{stamp}_{uuid.uuid4().hex[:6]}.jsonl")
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        if agent is not None:
            self.install(agent)

    def install(self, agent: Any) -> None:
        if not self.enabled:
            return
        self.agent = agent
        hooks = HookSystem()
        register_builtin_hooks(hooks)
        hooks.add_hook("trace_before_model", HookPoint.BEFORE_MODEL,
                       self._on_before_model, priority=900)
        hooks.add_hook("trace_after_model", HookPoint.AFTER_MODEL,
                       self._on_after_model, priority=900)
        hooks.add_hook("trace_after_tool", HookPoint.AFTER_TOOL_EXECUTION,
                       self._on_after_tool, priority=900)
        hooks.add_hook("trace_after_turn", HookPoint.AFTER_TURN,
                       self._on_after_turn, priority=900)
        agent._hook_system = hooks
        orchestrator = getattr(agent, "_chat_orchestrator", None)
        if orchestrator is not None:
            orchestrator._hook_system = hooks

    def _write(self, record: Dict[str, Any]) -> None:
        if not self.enabled or self._file is None:
            return
        self._seq += 1
        record.setdefault("seq", self._seq)
        record.setdefault("ts", time.time())
        record.setdefault("turn", self._turn)
        try:
            with self._lock:
                self._file.write(json.dumps(record, default=str) + "\n")
                self._file.flush()
        except Exception:
            pass

    def _est_tokens(self, messages: List[Dict[str, Any]]) -> Optional[int]:
        try:
            return int(self.agent.context_engine.token_estimator.count_messages_tokens(messages))
        except Exception:
            return None

    def _record(self, kind: str, **payload: Any) -> None:
        self._write({"type": kind, **payload})

    def begin_turn(self, user_input: Any, session_id: Optional[str] = None) -> None:
        if not self.enabled:
            return
        self._turn += 1
        self._iteration = 0
        self._session_id = session_id
        self._thinking = []
        self._tokens = []
        sessions_before = {}
        if self.agent is not None:
            for sid, sess in getattr(self.agent, "sessions", {}).items():
                sessions_before[sid] = len(sess.messages)
        self._record("turn_start",
                     user_input=_clip(str(user_input), 20000),
                     session_id=session_id,
                     sessions_before=sessions_before)
        if self._turn == 1 and self.agent is not None:
            provider_obj = getattr(self.agent, "provider", None)
            self._record("session_start",
                         provider=getattr(self.agent, "provider_name", None)
                             or getattr(provider_obj, "provider_name", None),
                         model=getattr(self.agent, "model", None)
                             or getattr(provider_obj, "model_name", None),
                         max_iterations=getattr(self.agent, "max_iterations", None),
                         tool_count=len(getattr(self.agent, "internal_tools", []) or []),
                         tools=sorted(str(t) for t in (getattr(self.agent, "internal_tools", []) or [])),
                         system_prompt=_clip(self.agent.default_system_message))

    def trace_event(self, ev: Any) -> None:
        if not self.enabled:
            return
        try:
            etype = ev.type
            data = ev.data or {}
            if etype == StreamEventType.RUN_STEP:
                self._iteration = int(data.get("iteration", 0) or 0)
            elif etype == StreamEventType.REASONING:
                self._thinking.append(str(data.get("delta", "")))
            elif etype == StreamEventType.TOKEN:
                self._tokens.append(str(data.get("delta", "")))
            elif etype == StreamEventType.USAGE:
                self._record("usage", iteration=self._iteration, usage=_safe(data))
            elif etype == StreamEventType.ERROR:
                self._flush_thinking("error")
                self._record("error", iteration=self._iteration, message=_clip(str(data.get("message", ""))))
            elif etype in (StreamEventType.TOOL_CALL_START, StreamEventType.MESSAGE_START,
                           StreamEventType.DONE):
                self._flush_thinking(str(etype.value))
        except Exception as exc:
            self._record("tracer_error", message=str(exc))

    def _flush_thinking(self, reason: str) -> None:
        if self._thinking:
            text = "".join(self._thinking)
            self._thinking = []
            if text.strip():
                self._record("thinking", iteration=self._iteration,
                             reason=reason, chars=len(text), text=_clip(text))
        if self._tokens and reason == "done":
            text = "".join(self._tokens)
            self._tokens = []
            if text.strip():
                self._record("streamed_text", iteration=self._iteration,
                             chars=len(text), text=_clip(text))

    def end_turn(self, final_response: Any, session_id: Optional[str] = None) -> None:
        if not self.enabled:
            return
        self._flush_thinking("turn_end")
        sid = session_id or self._session_id
        sessions_after = {}
        transcript = None
        system_prompt = None
        if self.agent is not None:
            for s_id, sess in getattr(self.agent, "sessions", {}).items():
                sessions_after[s_id] = len(sess.messages)
            if sid is None:
                ids = list(getattr(self.agent, "sessions", {}).keys())
                sid = ids[-1] if ids else None
            if sid is not None and sid in getattr(self.agent, "sessions", {}):
                sess = self.agent.sessions[sid]
                transcript = _safe(sess.messages)
                if sess.messages:
                    system_prompt = _clip(str(sess.messages[0].get("content", "")), 20000)
        self._record("turn_end",
                     session_id=sid,
                     final_response=_clip(str(final_response) if final_response is not None else ""),
                     sessions_after=sessions_after,
                     message_count=len(transcript) if transcript is not None else None,
                     est_tokens=self._est_tokens(transcript) if transcript else None,
                     system_prompt=system_prompt,
                     transcript=transcript)

    def _on_before_model(self, ctx: Any) -> HookResult:
        if ctx.session_id:
            self._session_id = ctx.session_id
        messages = _safe(ctx.messages)
        tools = [t.get("function", {}).get("name", "") if isinstance(t, dict) else str(t)
                 for t in (ctx.tools or [])]
        iteration = int((ctx.metadata or {}).get("iteration", 0) or 0)
        self._iteration = iteration
        self._record("llm_request",
                     session_id=ctx.session_id,
                     iteration=iteration,
                     message_count=len(messages),
                     est_tokens=self._est_tokens(ctx.messages),
                     tool_count=len(tools),
                     tools=tools,
                     messages=[{
                         "role": m.get("role"),
                         "name": m.get("name"),
                         "tool_call_id": m.get("tool_call_id"),
                         "chars": len(str(m.get("content", "") or "")),
                         "content": _clip(m.get("content", "")),
                         "tool_calls": _safe(m.get("tool_calls")) if m.get("tool_calls") else None,
                     } for m in messages])
        return HookResult()

    def _on_after_model(self, ctx: Any) -> HookResult:
        response = ctx.model_response
        payload: Dict[str, Any] = {
            "session_id": ctx.session_id,
            "iteration": int((ctx.metadata or {}).get("iteration", 0) or 0),
        }
        if response is not None:
            try:
                payload["role"] = response.role
                payload["content"] = _clip(response.content)
                payload["tool_calls"] = _safe(response.tool_calls)
                payload["usage"] = _safe(response.usage)
                extra = _safe(getattr(response, "extra", None) or {})
                if extra:
                    payload["extra_keys"] = sorted(extra.keys())
                    for key in ("reasoning", "reasoning_content", "thinking"):
                        if extra.get(key):
                            payload["reasoning"] = _clip(str(extra[key]))
            except Exception as exc:
                payload["serialize_error"] = str(exc)
        else:
            payload["content"] = _clip(str((ctx.metadata or {}).get("content", "")))
        payload["tool_calls"] = _safe(ctx.tool_calls) or payload.get("tool_calls")
        self._record("model_response", **payload)
        return HookResult()

    def _on_after_tool(self, ctx: Any) -> HookResult:
        result = ctx.tool_result if isinstance(ctx.tool_result, dict) else {"raw": ctx.tool_result}
        self._record("tool_call",
                     session_id=ctx.session_id,
                     iteration=int((ctx.metadata or {}).get("iteration", 0) or 0),
                     name=ctx.tool_name,
                     args=_safe(ctx.tool_args),
                     success=result.get("success", True),
                     result=_clip(result))
        return HookResult()

    def _on_after_turn(self, ctx: Any) -> HookResult:
        self._record("iteration_end",
                     session_id=ctx.session_id,
                     iteration=int((ctx.metadata or {}).get("iteration", 0) or 0),
                     tools_used=(ctx.metadata or {}).get("tools_used"),
                     successful_tools=(ctx.metadata or {}).get("successful_tools"),
                     session_message_count=len(ctx.messages or []))
        return HookResult()

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
