"""
AgentRunResult: the rich return value of ``Agent.stream_run``.

Mirrors OpenAI Agents SDK ``RunResultStreaming`` / LangGraph run object: a single
object that is **both** an async iterable of :class:`StreamEvent` (via
``stream_events()``) **and** awaitable for the final result.

The agent loop runs as a background task (the *producer*). Consumers drain
events via ``stream_events()``. Because the producer and consumer are decoupled
through the emitter queue, a UI that raises or stops early only affects its own
drain loop — the agent run can be cleanly cancelled via ``cancel()``.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterable, Optional

from .emitter import StreamEmitter
from .events import StreamEvent


class AgentRunResult:
    """Result of an async streaming agent run."""

    def __init__(self, emitter: StreamEmitter, task: "asyncio.Task", agent: Any = None):
        self._emitter = emitter
        self._task = task
        self._agent = agent
        self._consumed = False

    @property
    def session_id(self) -> Optional[str]:
        return self._emitter.session_id

    @property
    def run_id(self) -> Optional[str]:
        return self._emitter.run_id

    # --- Iterate events ---------------------------------------------------

    async def stream_events(self) -> AsyncIterable[StreamEvent]:
        """Drain streamed events until the run completes or is cancelled."""
        self._consumed = True
        try:
            async for ev in self._emitter:
                yield ev
        except (GeneratorExit, asyncio.CancelledError):
            # Consumer stopped early — stop the producer.
            self.cancel()
            raise
        # Make sure the producer has fully settled (e.g. exceptions surface).
        if not self._task.done():
            await self._task

    # --- Await final result ----------------------------------------------

    def __await__(self):
        return self._finalize().__await__()

    async def _finalize(self) -> Any:
        await self._task
        return self._emitter.final

    # --- Control ----------------------------------------------------------

    def cancel(self) -> None:
        """Cancel the underlying agent run and close the stream."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._emitter.close()

    @property
    def cancelled(self) -> bool:
        return self._task is not None and self._task.cancelled()


__all__ = ["AgentRunResult"]
