"""
SSE helpers for attaching the Logicore stream directly to a frontend.

Frontends typically consume Server-Sent Events. These helpers turn an emitter
(or any async iterable of :class:`StreamEvent`) into an async iterator of SSE
frames, so a developer can write (FastAPI example):

    @app.get("/chat")
    async def chat(q: str):
        run = await agent.stream_run(q, session_id="s1")
        return StreamingResponse(as_sse(run.stream_events()), media_type="text/event-stream")
"""

from __future__ import annotations

from typing import AsyncIterable, Iterable

from .events import StreamEvent

# Sentinel frame marking the end of the SSE stream.
SSE_DONE = "data: [DONE]\n\n"


def format_sse(event: StreamEvent) -> str:
    """Serialize a single event as an SSE frame."""
    return event.to_sse()


async def as_sse(source: AsyncIterable[StreamEvent], *, with_done: bool = True) -> AsyncIterable[str]:
    """
    Drain an async iterable of events into SSE frames.

    Args:
        source: async iterable of :class:`StreamEvent` (e.g. ``run.stream_events()``).
        with_done: emit a final ``data: [DONE]`` frame when the stream ends.
    """
    async for event in source:
        yield event.to_sse()
    if with_done:
        yield SSE_DONE


def events_to_sse(events: Iterable[StreamEvent], *, with_done: bool = True) -> Iterable[str]:
    """Synchronous variant for tests / non-async contexts."""
    for event in events:
        yield event.to_sse()
    if with_done:
        yield SSE_DONE


__all__ = ["format_sse", "as_sse", "events_to_sse", "SSE_DONE"]
