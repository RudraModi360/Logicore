# Streaming

Logicore provides a **first-class, provider-agnostic streaming API** so you can
attach an agent run directly to a frontend (SSE / WebSocket / terminal) and
render it live.

The model is inspired by the leading agentic frameworks:

- **LangGraph / LangChain v3** — a single async stream of typed, content-block
  events (`messages`, `tools`, `reasoning`, `custom`, `lifecycle`).
- **OpenAI Agents SDK** — `RunResultStreaming.stream_events()` returning a
  discriminated union of raw LLM events + semantic item events
  (`tool_called`, `tool_output`, `reasoning_item_created`, …).
- **Anthropic** — `content_block_start` → `content_block_delta`
  (`text_delta`, `thinking_delta`, `input_json_delta`) → `content_block_stop`.

Logicore unifies these into one event stream with a discriminated union on
`event["type"]`, plus a built-in SSE serializer and cancellation.

## Event model

Every event is a `StreamEvent` with `type`, `data`, `session_id`, `run_id`, and
`seq`. The event types are:

| `type` | `data` | meaning |
| --- | --- | --- |
| `run_start` | `{}` | run began |
| `run_step` | `{iteration, max_iterations}` | a new agentic iteration (kills "dead air" during tools) |
| `message_start` | `{iteration}` | a new LLM response turn |
| `token` | `{delta}` | assistant text delta |
| `reasoning` | `{delta}` | thinking / extended-thinking delta (Ollama, Gemini, Anthropic) |
| `tool_call_start` | `{name, call_id, args, iteration}` | a tool is dispatched |
| `tool_call_chunk` | `{call_id, name, args_delta}` | partial tool arguments (where the provider streams them) |
| `tool_call_end` | `{name, call_id, success, preview, iteration}` | tool finished (result preview) |
| `error` | `{message, recoverable}` | an error occurred |
| `usage` | `{...}` | token usage (when available) |
| `done` | `{content}` | final assembled message |
| `raw` | `{...provider native...}` | verbatim provider events (advanced UIs) |

`token`, `reasoning`, and `tool_call_chunk` events come straight from the
provider gateway. `run_start`, `run_step`, `message_start`, `tool_call_start`,
`tool_call_end`, `error`, and `done` are emitted by the orchestrator so they
work uniformly across **every provider** (OpenAI/Groq, Gemini, Ollama, Azure)
and **every agent** (`Agent`, `BasicAgent`, `CopilotAgent`, `SmartAgent`,
`MCPAgent`).

## No server required

Streaming is **in-process** by default. Every provider Logicore ships (Ollama,
OpenAI/Groq, Gemini, Azure) already streams tokens natively, so the agent
streams straight from the provider — **you do not need to run any server to
enable it**. A server (SSE/WebSocket) is only needed if you specifically want
to push those same events to a *browser*; it is an optional frontend detail,
not a prerequisite.

### In-process, async (preferred)

```python
from logicore import Agent

agent = Agent(provider="ollama", model="llama3.2:3b")

async for ev in agent.stream("summarize this repo", session_id="s1"):
    if ev.type == "token":
        print(ev.data["delta"], end="", flush=True)
    elif ev.type == "tool_call_start":
        print(f"\n[tool] {ev.data['name']}")
    elif ev.type == "done":
        print("\n[done]")
```

### In-process, synchronous (zero framework)

For plain scripts or apps with no event loop, use `stream_sync` — it streams
token-by-token via a callback and needs no server and no `async`:

```python
agent.stream_sync(
    "summarize this repo",
    on_event=lambda ev: (
        print(ev.data.get("delta", ""), end="", flush=True)
        if ev.type == "token" else None
    ),
)
```

### Await the final result

`stream_run` returns an `AgentRunResult` that is **both** an async iterator and
awaitable:

```python
run = await agent.stream_run("summarize this repo", session_id="s1")
async for ev in run.stream_events():
    ...  # render
final = await run            # final text
```

### Server-Sent Events (optional, browser-only)

```python
from logicore import as_sse

run = await agent.stream_run(q, session_id=session_id)
return StreamingResponse(as_sse(run.stream_events()), media_type="text/event-stream")
```

Each frame is `data: {"type": "...", "data": {...}}\n\n`. This is shown in
`examples/streaming_chatbot.py` purely as one possible frontend integration —
it is **not** required to use streaming. See `examples/streaming_cli.py` for a
server-free terminal example.

### Cancellation

```python
run = await agent.stream_run("...", session_id="s1")
# ... later, when the user hits "stop":
run.cancel()      # or agent.cancel_run(run)
```

The agent loop runs as a background task, so cancelling (or a client
disconnect) only affects the drain loop — never the agent internals. A faulty
UI sink is isolated and can never crash the run.

## Provider / capability support

| Capability | OpenAI/Groq | Gemini | Ollama | Azure (OpenAI) | Azure (Anthropic) |
| --- | --- | --- | --- | --- | --- |
| `token` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `reasoning` (thinking) | ✅* | ✅ | ✅ | ✅ | ✅ |
| `tool_call_chunk` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tool_call_start/end` | ✅ | ✅ | ✅ | ✅ | ✅ |

\* OpenAI `o`-series reasoning is surfaced when exposed by the provider.

## Provider-specific streaming formats

Each provider has a different streaming format. Logicore normalizes these into
the unified event types above, but there are important differences:

### Ollama
- **Content tokens**: `message.content` field
- **Thinking tokens**: `message.thinking` field (when `think=True` is enabled)
- **Tool calls**: Complete objects (not incremental JSON)
- **Configuration**: Use `treat_thinking_as_content=True` to treat thinking tokens as normal content (useful for models that don't properly separate thinking)

```python
from logicore import Agent

agent = Agent(provider="ollama", model="llama3.2:3b")

# Option 1: Treat thinking as content (recommended for most models)
agent._provider._gateway.treat_thinking_as_content = True

# Option 2: Enable thinking explicitly
agent._provider._gateway.think = True
```

### OpenAI / Groq
- **Content tokens**: `choices[0].delta.content`
- **Thinking tokens**: `choices[0].delta.reasoning_content` (non-standard, used by vLLM, DeepSeek)
- **Tool calls**: Incremental JSON fragments with `index` field

### Gemini
- **Content tokens**: `candidates[0].content.parts[].text`
- **Thinking tokens**: Parts with `thought: true` flag
- **Tool calls**: Complete objects with `functionCall`

### Anthropic (via Azure)
- **Content blocks**: Different types (`text`, `thinking`, `tool_use`)
- **Delta types**: `text_delta`, `thinking_delta`, `input_json_delta`
- **Thinking**: Includes signature for verification

## Backward compatibility

The legacy callback API is unchanged: `agent.chat(..., stream=True,
streaming_funct=cb)` and `set_callbacks(on_token=..., on_tool_start=...,
on_tool_end=..., on_final_message=...)` continue to work. The new event stream
is strictly additive.
