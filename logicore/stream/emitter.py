"""
StreamEmitter: per-run async event bus.

The emitter is the single channel through which gateways and the orchestrator
publish :class:`StreamEvent` objects. Consumers drain it with ``async for``.

Design notes
------------
* The producer (the agent run) and the consumer (the UI drain) are decoupled
  through an ``asyncio.Queue``. This gives **natural backpressure** (a slow UI
  blocks the producer on ``queue.get``) and **isolation**: a consumer that
  raises only affects its own drain loop, never the agent loop (the agent loop
  runs as a separate task — see ``Agent.stream_run``).
* A ``None`` sentinel marks the end of the stream.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .events import StreamEvent, StreamEventType


class StreamEmitter:
    """Async event bus for a single agent run."""

    def __init__(self, session_id: Optional[str] = None, run_id: Optional[str] = None):
        self.session_id = session_id
        self.run_id = run_id
        self._queue: "asyncio.Queue[Optional[StreamEvent]]" = asyncio.Queue()
        self._seq = 0
        self.final: Any = None
        self.usage: Optional[Dict[str, Any]] = None
        self._closed = False

    # --- Publishing -------------------------------------------------------

    def _normalize(self, event: Any) -> StreamEvent:
        if isinstance(event, StreamEvent):
            ev = event
        elif isinstance(event, dict):
            ev = StreamEvent.from_dict(event)
        else:
            raise TypeError(f"Cannot emit non-event object: {type(event)!r}")
        # Inject run/session scope if missing.
        if ev.session_id is None:
            ev.session_id = self.session_id
        if ev.run_id is None:
            ev.run_id = self.run_id
        if ev.seq == 0:
            self._seq += 1
            ev.seq = self._seq
        return ev

    def emit(self, event: Any) -> None:
        """Publish an event synchronously (safe from sync gateway code paths)."""
        if self._closed:
            return
        self._queue.put_nowait(self._normalize(event))

    async def emit_async(self, event: Any) -> None:
        if self._closed:
            return
        await self._queue.put(self._normalize(event))

    def close(self) -> None:
        """Signal end-of-stream by pushing the sentinel."""
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)

    # --- Consuming --------------------------------------------------------

    def __aiter__(self):
        return self._drain()

    async def _drain(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    # --- Convenience constructors ----------------------------------------

    def make(self, type: "StreamEventType | str", data: Optional[Dict[str, Any]] = None) -> StreamEvent:
        """Build a scoped :class:`StreamEvent` without emitting it."""
        return StreamEvent.create(type, data, session_id=self.session_id, run_id=self.run_id)


__all__ = ["StreamEmitter"]
