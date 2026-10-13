"""Tests for the standalone prototype agent tools and its JSONL tracer."""

import json
import os

import pytest

from experiments.prototype_agent import (
    MAX_TOOL_OUTPUT_CHARS,
    TRACE_MAX_FIELD,
    PrototypeTracer,
    ModelClient,
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    _normalise_duckduckgo_url,
    _parse_duckduckgo_results,
    estimate_tokens,
    run_turn,
)


def test_parse_duckduckgo_results_handles_nested_markup():
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fscore">
        India <b>vs Pakistan</b>
      </a>
      <a class="result__snippet">India won by <b>six runs</b>.</a>
    </div>
    """

    results = _parse_duckduckgo_results(html)

    assert results == [
        {
            "href": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fscore",
            "title": "India vs Pakistan",
            "snippet": "India won by six runs.",
        }
    ]


def test_normalise_duckduckgo_url_unwraps_redirect():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fscore"

    assert _normalise_duckduckgo_url(href) == "https://example.com/score"


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
class FakeModel(ModelClient):
    """Replays canned responses so loop behaviour is tested without a provider."""

    def __init__(self, script):
        super().__init__("gemini", "fake-model")
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None, max_tokens=2048):
        self.calls += 1
        return self.script.pop(0) if self.script else {
            "content": "fallback", "tool_calls": [], "usage": {}}


def _call(call_id, name, args):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


def _session():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def test_run_turn_executes_tool_then_answers(tmp_path):
    target = tmp_path / "made.txt"
    model = FakeModel([
        {"content": "", "tool_calls": [_call("1", "create_file",
                                             {"file_path": str(target), "content": "v1"})]},
        {"content": "", "tool_calls": [_call("2", "read_file",
                                             {"file_path": str(target)})]},
        {"content": "created and verified", "tool_calls": []},
    ])
    session = _session()

    final = run_turn(model, session, "make then read it")

    assert final == "created and verified"
    assert model.calls == 3
    assert target.read_text(encoding="utf-8") == "v1"
    assert [m["role"] for m in session] == [
        "system", "user", "assistant", "tool", "assistant", "tool", "assistant"]


def test_run_turn_reports_tool_error_to_model():
    model = FakeModel([
        {"content": "", "tool_calls": [_call("1", "read_file", {"file_path": "/nope.txt"})]},
        {"content": "file absent", "tool_calls": []},
    ])

    assert run_turn(model, _session(), "read a missing file") == "file absent"


def test_run_turn_handles_unknown_tool_and_broken_arguments():
    model = FakeModel([
        {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "no_such_tool", "arguments": "{}"}},
            {"id": "2", "function": {"name": "bash", "arguments": "{not json"}},
        ]},
        {"content": "recovered", "tool_calls": []},
    ])
    session = _session()

    assert run_turn(model, session, "use broken tools") == "recovered"
    tool_messages = [m for m in session if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert all(json.loads(m["content"])["success"] is False for m in tool_messages)


def test_run_turn_stops_at_max_iterations():
    always_tool = {"content": "", "tool_calls": [_call("1", "bash",
                                                       {"command": "echo tick"})]}
    model = FakeModel([dict(always_tool) for _ in range(10)])
    session = _session()

    assert run_turn(model, session, "loop forever", max_iterations=3) == "(max iterations reached)"
    assert model.calls == 3


def test_raising_tool_is_contained_not_fatal(tmp_path):
    def _boom(_args):
        raise RuntimeError("kaboom")

    import experiments.prototype_agent as proto
    original = proto.TOOL_IMPLS["bash"]
    proto.TOOL_IMPLS["bash"] = _boom
    try:
        model = FakeModel([
            {"content": "", "tool_calls": [_call("1", "bash", {"command": "x"})]},
            {"content": "recovered", "tool_calls": []},
        ])
        session = _session()
        assert run_turn(model, session, "run a broken tool") == "recovered"
        payload = json.loads([m for m in session if m["role"] == "tool"][0]["content"])
        assert payload["success"] is False
        assert "RuntimeError" in payload["content"]
    finally:
        proto.TOOL_IMPLS["bash"] = original


# --------------------------------------------------------------------------- #
# Tracer
# --------------------------------------------------------------------------- #
def _read_trace(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_tracer_writes_ordered_readable_trace(tmp_path):
    target = tmp_path / "traced.txt"
    path = tmp_path / "trace.jsonl"
    tracer = PrototypeTracer(path=str(path), provider="gemini", model="fake-model")
    tracer.session_start(SYSTEM_PROMPT, max_iterations=8)
    session = _session()
    model = FakeModel([
        {"content": "", "tool_calls": [_call("1", "create_file",
                                             {"file_path": str(target), "content": "hi"})]},
        {"content": "done", "tool_calls": []},
    ])
    run_turn(model, session, "make a file", tracer=tracer)
    tracer.close()

    records = _read_trace(path)
    assert [r["type"] for r in records] == [
        "session_start", "turn_start", "llm_request", "model_response",
        "tool_call", "llm_request", "model_response", "turn_end",
    ]
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    assert all(r["ts"] > 0 for r in records)

    start = records[0]
    assert start["provider"] == "gemini"
    assert start["tool_count"] == len(TOOL_SCHEMAS)

    tool = next(r for r in records if r["type"] == "tool_call")
    assert tool["name"] == "create_file"
    assert tool["success"] is True
    assert tool["duration_ms"] >= 0
    assert tool["args"]["content"] == "hi"

    request = next(r for r in records if r["type"] == "llm_request")
    assert request["est_tokens"] > 0
    assert request["tools"] == [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert {m["role"] for m in request["messages"]} == {"system", "user"}

    end = records[-1]
    assert end["final_response"] == "done"
    assert end["iterations"] == 2
    assert end["transcript"]


def test_tracer_marks_failed_tools_and_counts_them(tmp_path):
    path = tmp_path / "trace_err.jsonl"
    tracer = PrototypeTracer(path=str(path), provider="ollama", model="m")
    tracer.session_start(SYSTEM_PROMPT)
    model = FakeModel([
        {"content": "", "tool_calls": [_call("1", "read_file", {"file_path": "/missing"})]},
        {"content": "recovered", "tool_calls": []},
    ])
    run_turn(model, _session(), "read missing", tracer=tracer)
    summary = tracer.summary()
    tracer.close()

    tool = next(r for r in _read_trace(path) if r["type"] == "tool_call")
    assert tool["success"] is False
    assert summary["tool_errors"] == 1
    assert summary["tool_calls"] == 1
    assert summary["turns"] == 1
    assert summary["llm_calls"] == 2


def test_tracer_records_malformed_tool_arguments(tmp_path):
    path = tmp_path / "trace_args.jsonl"
    tracer = PrototypeTracer(path=str(path), provider="ollama", model="m")
    tracer.session_start(SYSTEM_PROMPT)
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "1", "function": {"name": "bash",
                                                                "arguments": "{oops"}}]},
        {"content": "ok", "tool_calls": []},
    ])
    run_turn(model, _session(), "bad args", tracer=tracer)
    tracer.close()

    tool = next(r for r in _read_trace(path) if r["type"] == "tool_call")
    assert tool["args_parsed"] is False
    assert tool["args"]["_raw"] == "{oops"


def test_tracer_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOTYPE_TRACE", "0")
    tracer = PrototypeTracer(provider="ollama", model="m")
    assert tracer.enabled is False
    assert tracer.path is None

    tracer.session_start(SYSTEM_PROMPT)
    tracer.turn_start("hello", [])
    tracer.llm_request([], TOOL_SCHEMAS)
    tracer.tool_call("bash", {}, "ok", 1.0)
    tracer.turn_end("ok", [], 1)
    tracer.close()  # must not raise


def test_tracer_clips_long_fields(tmp_path):
    path = tmp_path / "trace_big.jsonl"
    tracer = PrototypeTracer(path=str(path), provider="ollama", model="m")
    tracer.session_start(SYSTEM_PROMPT)
    tracer.tool_call("bash", {"command": "x" * (TRACE_MAX_FIELD + 500)},
                     "y" * (TRACE_MAX_FIELD + 500), 1.0)
    tracer.close()

    tool = next(r for r in _read_trace(path) if r["type"] == "tool_call")
    assert "trace truncated" in json.dumps(tool["args"])
    assert "trace truncated" in tool["result"]


def test_estimate_tokens_grows_with_history():
    small = [{"role": "user", "content": "hi"}]
    large = [{"role": "user", "content": "x" * 4000}]
    assert estimate_tokens(small) > 0
    assert estimate_tokens(large) > estimate_tokens(small)
