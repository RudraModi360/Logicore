"""
Streaming event protocol for the Logicore agentic harness.

This module defines the typed event model used to stream agent execution to
frontends (SSE / WebSocket / terminal). It mirrors the industry pattern of a
single async-iterable stream carrying *semantic* events (token, reasoning,
tool lifecycle, done) plus optional *raw* provider events, so a developer can
attach the stream directly to a UI.

Event model (inspired by LangGraph event streaming, OpenAI Agents SDK
RunItemStreamEvent, and Anthropic content-block streaming):

    run_start
    run_step            # per agentic iteration (kills "dead air" during tools)
    message_start       # a new LLM response turn begins
    token               # assistant text delta
    reasoning           # thinking / extended-thinking delta
    tool_call_start     # a tool call is dispatched
    tool_call_chunk     # partial tool arguments (where the provider streams them)
    tool_call_end       # tool finished (result preview)
    error               # recoverable / terminal error
    usage               # token usage for the turn
    done                # final assembled message

Every event is a discriminated union on ``event["type"]`` so consumers (and
type checkers) can narrow on it exactly like LangChain's ``StreamPart``.
"""

from __future__ import annotations

import time
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class StreamEventType(str, Enum):
    """Discriminated union of stream event types (string-valued for JSON/SSE)."""

    RUN_START = "run_start"
    RUN_STEP = "run_step"
    MESSAGE_START = "message_start"
    TOKEN = "token"
    REASONING = "reasoning"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_CHUNK = "tool_call_chunk"
    TOOL_CALL_END = "tool_call_end"
    ERROR = "error"
    USAGE = "usage"
    DONE = "done"
    # Raw, provider-native events forwarded verbatim for advanced UIs.
    RAW = "raw"

    def __str__(self) -> str:  # so it serializes as the bare string value
        return self.value


@dataclass
class StreamEvent:
    """
    A single streaming event.

    ``type`` is a :class:`StreamEventType` (also comparable to its string value).
    ``data`` is a provider/event-specific payload dict. ``seq`` is a monotonic
    sequence number for the run, ``run_id`` ties events to a single agent run,
    and ``session_id`` scopes the event to a conversation.
    """

    type: StreamEventType
    data: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    seq: int = 0
    timestamp: float = field(default_factory=lambda: time.time())

    # --- Constructors -----------------------------------------------------

    @classmethod
    def create(
        cls,
        type: "StreamEventType | str",
        data: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> "StreamEvent":
        if isinstance(type, str):
            type = StreamEventType(type)
        return cls(
            type=type,
            data=data or {},
            session_id=session_id,
            run_id=run_id,
        )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StreamEvent":
        t = d.get("type")
        if isinstance(t, StreamEventType):
            type_ = t
        else:
            type_ = StreamEventType(str(t))
        return cls(
            type=type_,
            data=d.get("data", {}) or {},
            session_id=d.get("session_id"),
            run_id=d.get("run_id"),
            seq=int(d.get("seq", 0) or 0),
            timestamp=float(d.get("timestamp", time.time())),
        )

    # --- Serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": str(self.type),
            "data": self.data,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    def to_sse(self) -> str:
        """Serialize this event as a Server-Sent Events frame."""
        return f"data: {self.to_json()}\n\n"

    def __str__(self) -> str:
        return self.to_json()


__all__ = ["StreamEventType", "StreamEvent"]
