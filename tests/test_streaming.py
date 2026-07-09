"""
Tests for the Logicore streaming layer.

Covers:
* StreamEvent serialization (dict / SSE).
* StreamEmitter drain + sentinel + consumer isolation.
* SSE helpers.
* AgentRunResult (await final + cancel).
* End-to-end event ordering through ChatOrchestrator with a fake gateway that
  streams tokens and exercises a tool call.
"""

import asyncio
import pytest

from logicore.stream.events import StreamEvent, StreamEventType
from logicore.stream.emitter import StreamEmitter
from logicore.stream.sse import as_sse, events_to_sse, SSE_DONE
from logicore.stream.result import AgentRunResult
from logicore.gateway.base import NormalizedMessage, ProviderGateway


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def test_event_to_dict_and_sse():
    ev = StreamEvent.create(StreamEventType.TOKEN, {"delta": "hi"}, session_id="s1", run_id="r1")
    d = ev.to_dict()
    assert d["type"] == "token"
    assert d["data"]["delta"] == "hi"
    assert d["session_id"] == "s1"
    assert d["run_id"] == "r1"
    assert ev.to_sse().startswith("data: ") and ev.to_sse().endswith("\n\n")


def test_event_from_dict_roundtrip():
    ev = StreamEvent.create("done", {"content": "x"})
    ev2 = StreamEvent.from_dict(ev.to_dict())
    assert ev2.type == StreamEventType.DONE
    assert ev2.data["content"] == "x"


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emitter_drains_and_sentinel():
    em = StreamEmitter(session_id="s")
    em.emit(StreamEvent.create("token", {"delta": "a"}))
    em.emit(StreamEvent.create("token", {"delta": "b"}))
    em.close()
    got = [e async for e in em]
    assert [e.data["delta"] for e in got] == ["a", "b"]


@pytest.mark.asyncio
async def test_emitter_isolates_consumer_errors():
    # A consumer that raises must NOT propagate into the producer (emit is sync).
    em = StreamEmitter()
    em.emit(StreamEvent.create("token", {"delta": "x"}))
    em.close()
    with pytest.raises(RuntimeError):
        async for _ in em:
            raise RuntimeError("consumer boom")
    # Producer path is unaffected:
    em2 = StreamEmitter()
    em2.emit(StreamEvent.create("token", {"delta": "y"}))
    em2.close()
    items = [e async for e in em2]
    assert items[0].data["delta"] == "y"


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

def test_events_to_sse():
    events = [StreamEvent.create("token", {"delta": "a"})]
    frames = list(events_to_sse(events))
    assert frames[0].startswith("data: ")
    assert frames[-1] == SSE_DONE


@pytest.mark.asyncio
async def test_as_sse_async():
    em = StreamEmitter()
    em.emit(StreamEvent.create("token", {"delta": "a"}))
    em.close()
    frames = [f async for f in as_sse(em)]
    assert frames[-1] == SSE_DONE
    assert "token" in frames[0]


# ---------------------------------------------------------------------------
# AgentRunResult
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_result_await_final():
    em = StreamEmitter()
    captured = []

    async def producer():
        em.emit(StreamEvent.create("token", {"delta": "hello"}))
        em.final = "hello world"
        em.close()

    task = asyncio.ensure_future(producer())
    run = AgentRunResult(em, task)
    final = await run
    assert final == "hello world"


@pytest.mark.asyncio
async def test_run_result_cancel():
    em = StreamEmitter()

    async def producer():
        await asyncio.sleep(10)  # would run forever
        em.close()

    task = asyncio.ensure_future(producer())
    run = AgentRunResult(em, task)
    run.cancel()
    await asyncio.sleep(0.01)
    assert task.cancelled() or task.done()


# ---------------------------------------------------------------------------
# Orchestrator integration (fake gateway + tool call)
# ---------------------------------------------------------------------------

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo_tool",
        "description": "echo",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
}


class FakeGateway(ProviderGateway):
    def __init__(self):
        self._calls = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        return NormalizedMessage(role="assistant", content="final answer", tool_calls=[])

    async def chat_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None):
        self._calls += 1
        if on_event:
            on_event({"type": "token", "data": {"delta": "part1 "}})
            on_event({"type": "token", "data": {"delta": "part2"}})
        if self._calls == 1:
            return NormalizedMessage(
                role="assistant", content="",
                tool_calls=[{"id": "c1", "type": "function",
                             "function": {"name": "echo_tool", "arguments": '{"text":"hi"}'}}],
            )
        return NormalizedMessage(role="assistant", content="final answer", tool_calls=[])


class FakeToolExecutor:
    async def get_all_tools(self, *a, **k):
        return [TOOL_SCHEMA]

    def parse_tool_arguments(self, name, args):
        if isinstance(args, str):
            try:
                return json.loads(args), None
            except Exception:
                return args, "parse error"
        return args, None

    async def execute(self, name, args, session_id, cache=None):
        return {"success": True, "content": f"echo:{args}"}

    def normalize_tool_result(self, name, result):
        return result


class FakeContextEngine:
    async def prepare_messages(self, messages, session_id=None):
        return None, messages

    def remove_hint(self, messages, hint):
        return None


class FakeSession:
    def __init__(self):
        self.messages = []
        self.metadata = {}

    def add_message(self, msg):
        self.messages.append(msg)


class FakeAgent:
    def __init__(self):
        self.gateway = FakeGateway()
        self.callbacks = {"on_token": None, "on_tool_start": None,
                          "on_tool_end": None, "on_final_message": None}
        self.supports_tools = True
        self.disabled_tools = set()
        self.internal_tools = [TOOL_SCHEMA]
        self.tool_executor = FakeToolExecutor()
        self.context_engine = FakeContextEngine()
        self._reasoning_controller = None
        self.telemetry_enabled = False
        self.execution_log = []
        self._task_manager = None
        self.provider = type("P", (), {"provider_name": "fake"})()
        self.model_name = "fake"
        self.max_iterations = 5
        self._sessions = {}

    def get_session(self, session_id):
        if session_id not in self._sessions:
            self._sessions[session_id] = FakeSession()
        return self._sessions[session_id]

    async def input_enricher_enrich(self, x):
        return x

    def _serialize_tool_result_for_model(self, name, result):
        return str(result.get("content", ""))

    def _build_reminder_routing_hint(self, text, tool_names):
        return None

    def _normalize_tool_paths(self, session, name, args):
        return args

    def _is_reminder_like_request(self, text):
        return False

    def _has_unverified_reminder_claim(self, content):
        return False

    def _update_tool_directory_context(self, session, name, args, result):
        return None

    def _generate_execution_summary(self):
        return "summary"


@pytest.mark.asyncio
async def test_orchestrator_event_order_via_wrapper():
    from logicore.agent.chat_orchestrator import ChatOrchestrator

    agent = FakeAgent()
    orchestrator = ChatOrchestrator(agent=agent)

    class _Enricher:
        async def enrich_async(self, x):
            return x
    orchestrator.input_enricher = _Enricher()

    collected = []
    em = StreamEmitter(session_id="s1")

    # Wrap emit to record. Gateway events arrive as dicts; orchestrator
    # events arrive as StreamEvent objects.
    orig_emit = em.emit
    def record(ev):
        t = ev["type"] if isinstance(ev, dict) else ev.type
        collected.append(StreamEventType(t))
        orig_emit(ev)
    em.emit = record

    final = await orchestrator.run(
        user_input="do something",
        session_id="s1",
        emitter=em,
    )

    assert final == "final answer"
    assert StreamEventType.RUN_START in collected
    assert StreamEventType.RUN_STEP in collected
    assert StreamEventType.MESSAGE_START in collected
    assert StreamEventType.TOKEN in collected
    assert StreamEventType.TOOL_CALL_START in collected
    assert StreamEventType.TOOL_CALL_END in collected
    assert collected[-1] == StreamEventType.DONE
    # token events must appear before the final DONE
    done_idx = collected.index(StreamEventType.DONE)
    assert all(t != StreamEventType.TOOL_CALL_END or i < done_idx for i, t in enumerate(collected))
    # run_step appears at least twice (two LLM iterations)
    assert collected.count(StreamEventType.RUN_STEP) >= 2


# ---------------------------------------------------------------------------
# Public Agent.stream_run glue (producer task + AgentRunResult)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_stream_run_glue():
    from logicore.agent.base import Agent

    # Build an Agent without running its heavy __init__.
    agent = Agent.__new__(Agent)
    agent.callbacks = {"on_token": None, "on_tool_start": None,
                       "on_tool_end": None, "on_final_message": None}
    agent.execution_log = []

    class _Enricher:
        async def enrich_async(self, x):
            return x
    agent.input_enricher = _Enricher()
    agent._ensure_task_manager = lambda *a, **k: None

    class FakeOrch:
        async def run(self, *, user_input, session_id, callbacks,
                      generate_walkthrough, emitter, **kwargs):
            emitter.emit(StreamEvent.create("run_start", {}))
            emitter.emit(StreamEvent.create("token", {"delta": "hello "}))
            emitter.emit(StreamEvent.create("token", {"delta": "world"}))
            emitter.emit(StreamEvent.create("done", {"content": "hello world"}))
            return "hello world"

    agent._chat_orchestrator = FakeOrch()

    run = await agent.stream_run("hi", session_id="s1")
    events = [ev async for ev in run.stream_events()]
    assert [e.type for e in events] == [
        StreamEventType.RUN_START, StreamEventType.TOKEN,
        StreamEventType.TOKEN, StreamEventType.DONE,
    ]
    final = await run
    assert final == "hello world"


@pytest.mark.asyncio
async def test_agent_stream_run_cancel():
    from logicore.agent.base import Agent

    agent = Agent.__new__(Agent)
    agent.callbacks = {}
    agent.execution_log = []

    class _Enricher:
        async def enrich_async(self, x):
            return x
    agent.input_enricher = _Enricher()
    agent._ensure_task_manager = lambda *a, **k: None

    class FakeOrch:
        async def run(self, *, user_input, session_id, callbacks,
                      generate_walkthrough, emitter, **kwargs):
            emitter.emit(StreamEvent.create("token", {"delta": "x"}))
            await asyncio.sleep(30)  # simulate long run
            emitter.emit(StreamEvent.create("done", {"content": "never"}))
            return "never"

    agent._chat_orchestrator = FakeOrch()

    run = await agent.stream_run("hi", session_id="s1")
    # Consume one event then break early -> should cancel the producer.
    gen = run.stream_events()
    await gen.__anext__()
    await gen.aclose()
    await asyncio.sleep(0.02)
    assert run._task.cancelled() or run._task.done()


# ---------------------------------------------------------------------------
# stream_sync: server-free, synchronous streaming (plain script usage)
# ---------------------------------------------------------------------------

def test_agent_stream_sync_no_server():
    from logicore.agent.base import Agent

    # No running event loop here (sync test), so stream_sync drives its own.
    agent = Agent.__new__(Agent)
    agent.callbacks = {}
    agent.execution_log = []

    class _Enricher:
        async def enrich_async(self, x):
            return x
    agent.input_enricher = _Enricher()
    agent._ensure_task_manager = lambda *a, **k: None

    class FakeOrch:
        async def run(self, *, user_input, session_id, callbacks,
                      generate_walkthrough, emitter, **kwargs):
            emitter.emit(StreamEvent.create("token", {"delta": "hello "}))
            emitter.emit(StreamEvent.create("token", {"delta": "world"}))
            emitter.emit(StreamEvent.create("done", {"content": "hello world"}))
            return "hello world"

    agent._chat_orchestrator = FakeOrch()

    seen = []
    final = agent.stream_sync("hi", session_id="s1", on_event=lambda ev: seen.append(ev.type))
    assert seen == [StreamEventType.TOKEN, StreamEventType.TOKEN, StreamEventType.DONE]
    assert final == "hello world"

