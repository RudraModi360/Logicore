"""
Base gateway classes and shared utilities.

Provides the abstract ProviderGateway and NormalizedMessage format
used by all provider gateways.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
import asyncio
import inspect
import logging
import re as _re

logger = logging.getLogger(__name__)


def _gateway_debug(gateway: "ProviderGateway", message: str):
    """Emit gateway debug logs only when provider debug mode is enabled."""
    if getattr(gateway.provider, "debug", False):
        logger.debug(f"[Gateway:{gateway.provider_name}] {message}")


class NormalizedMessage:
    """Standard message format returned by all gateways."""

    def __init__(self, role: str, content: str = "", tool_calls: List[Dict[str, Any]] = None,
                 name: str = None, tool_call_id: str = None, extra: Dict[str, Any] = None,
                 usage: Optional[Dict[str, Any]] = None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.name = name
        self.tool_call_id = tool_call_id
        self.extra = extra or {}
        self.usage = usage

    def to_dict(self) -> Dict[str, Any]:
        result = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.name:
            result["name"] = self.name
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.extra:
            result.update(self.extra)
        return result


class ProviderGateway(ABC):
    """Abstract base for all provider gateways."""

    def __init__(self, provider):
        self.provider = provider
        self.model_name = (
            provider.get_model_name()
            if hasattr(provider, "get_model_name")
            else provider.model_name
        )
        self.provider_name = getattr(provider, "provider_name", "unknown")

    @abstractmethod
    async def chat(
        self, messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
    ) -> NormalizedMessage:
        pass

    @abstractmethod
    async def chat_stream(
        self, messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        max_tokens: Optional[int] = None,
    ) -> NormalizedMessage:
        pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _dispatch_token(on_token, token):
    """Call on_token callback, handling both sync and async."""
    if on_token:
        if inspect.iscoroutinefunction(on_token):
            await on_token(token)
        else:
            on_token(token)


async def _dispatch_event(on_event, type: str, data: Dict[str, Any]) -> None:
    """
    Emit a structured stream event to the consumer's ``on_event`` sink.

    ``on_event`` receives a plain dict ``{"type": ..., "data": ...}`` so it can
    be forwarded straight into a :class:`~logicore.stream.emitter.StreamEmitter`.
    Handles both sync and async sinks. Exceptions in the sink are swallowed so a
    broken UI callback can never break the agent loop.

    **Suspension guarantee** — even when ``on_event`` is a sync callback, this
    function yields control back to the event loop so a concurrent consumer (e.g.
    ``async for ev in run.stream_events()``) can drain the event. Without this,
    all events pile up in the asyncio queue and burst out together once the
    provider stream ends, defeating the purpose of streaming.
    """
    if not on_event:
        return
    event = {"type": str(type), "data": data or {}}
    try:
        if inspect.iscoroutinefunction(on_event):
            await on_event(event)
        else:
            on_event(event)
            await asyncio.sleep(0)  # yield so consumer can drain the event
    except Exception as exc:
        # Isolation: a failing consumer must not crash the provider stream.
        logger.warning("on_event callback raised %s: %s", type(exc).__name__, exc)


async def _dispatch_stream_text(on_token, text: str):
    """Emit streamed text progressively to ensure visible token-level updates."""
    if not text:
        return
    await _dispatch_token(on_token, text)


async def _dispatch_stream_reasoning(on_token, on_event, text: str):
    """Dual-dispatch reasoning/thinking tokens.

    Like tokens, reasoning is emitted through *both* channels so that legacy
    ``on_token`` callbacks (``chat(streaming_funct=...)``) receive thinking text
    when no ``on_event`` sink is available.  When ``on_event`` is set the
    structured ``reasoning`` event is the primary path; ``on_token`` serves as
    a fallback for consumers that only implement the raw-callback interface.
    """
    if not text:
        return
    await _dispatch_event(on_event, "reasoning", {"delta": text})
    if not on_event:
        await _dispatch_token(on_token, text)


# Keys that belong to non-OpenAI providers (Anthropic, Gemini, ...).
# OpenAI-compatible APIs (OpenAI, Groq, etc.) reject them with a 400.
_OPENAI_UNSUPPORTED_MESSAGE_KEYS = ("tool_call_ids", "gemini_content")


def _strip_provider_specific_fields(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove message keys that are unsupported by OpenAI-compatible APIs.

    The orchestrator emits provider-agnostic messages that may carry fields
    specific to Anthropic (``tool_call_ids``) or Gemini (``gemini_content``).
    Forwarding those to OpenAI/Groq yields a 400 such as
    ``property 'tool_call_ids' is unsupported``. This returns a new list with
    those keys removed so OpenAI-style providers stay happy, while other
    providers keep using the keys untouched.
    """
    cleaned = []
    for msg in messages:
        if isinstance(msg, dict) and any(k in msg for k in _OPENAI_UNSUPPORTED_MESSAGE_KEYS):
            msg = {k: v for k, v in msg.items() if k not in _OPENAI_UNSUPPORTED_MESSAGE_KEYS}
        cleaned.append(msg)
    return cleaned


def _serialize_tool_call_arguments(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ensure every assistant tool_call's ``function.arguments`` is a JSON string.

    The orchestrator may store already-parsed tool calls (``arguments`` as a
    dict/object) when echoing assistant turns back to the model. OpenAI-
    compatible APIs require ``arguments`` to be a JSON-encoded string, and
    reject objects with a 400 (``value must be a string``). This re-serializes
    any non-string arguments so the request is valid for OpenAI/Groq.
    """
    import json

    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            cleaned.append(msg)
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            cleaned.append(msg)
            continue
        fixed_calls = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                fixed_calls.append(tc)
                continue
            tc = dict(tc)
            func = tc.get("function")
            if isinstance(func, dict):
                args = func.get("arguments")
                if args is not None and not isinstance(args, str):
                    try:
                        func = dict(func)
                        func["arguments"] = json.dumps(args)
                        tc["function"] = func
                    except (TypeError, ValueError):
                        pass
            fixed_calls.append(tc)
        new_msg = dict(msg)
        new_msg["tool_calls"] = fixed_calls
        cleaned.append(new_msg)
    return cleaned


def _convert_local_images_to_base64(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert local image file paths to base64 data URLs (OpenAI-compatible APIs)."""
    import os
    import base64
    import mimetypes

    converted = []
    for msg in messages:
        new_msg = msg.copy()
        content = msg.get("content")

        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    new_part = part.copy()
                    image_url_data = part.get("image_url")
                    url = image_url_data.get("url") if isinstance(image_url_data, dict) else image_url_data

                    if url and not str(url).startswith(("http://", "https://", "data:")):
                        local_path = str(url).replace("\\\\", "\\")
                        if os.path.isfile(local_path):
                            try:
                                with open(local_path, "rb") as f:
                                    data = f.read()
                                mime_type, _ = mimetypes.guess_type(local_path)
                                mime_type = mime_type or "image/png"
                                encoded = base64.b64encode(data).decode("utf-8")
                                new_part["image_url"] = {"url": f"data:{mime_type};base64,{encoded}"}
                            except Exception:
                                pass
                    new_content.append(new_part)
                elif isinstance(part, dict) and part.get("type") == "image":
                    data = part.get("data", "")
                    url = data if isinstance(data, str) and data.startswith("data:") else f"data:{part.get('mime_type', 'image/png')};base64,{data}"
                    new_content.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    new_content.append(part)
            new_msg["content"] = new_content

        converted.append(new_msg)
    return converted


def _normalize_openai_tool_calls(raw_tool_calls) -> List[Dict[str, Any]]:
    """Convert SDK tool_calls objects *or* plain dicts to standard dicts.

    Several callers hand us raw JSON instead of SDK objects (the Azure
    Inference HTTP fallback, OpenAI-compatible proxies, mocks/tests). Those
    dict-shaped calls must not be silently dropped: a lost tool call makes the
    orchestrator see ``tool_calls == []`` and treat the response as the final
    answer, so the run stops right where the model tried to call a tool and
    control bounces back to the user for the next turn.
    """
    tool_calls = []
    if not raw_tool_calls:
        return tool_calls

    for tc in raw_tool_calls:
        if isinstance(tc, dict):
            func = tc.get("function")
            if isinstance(func, dict):
                name = func.get("name")
                arguments = func.get("arguments")
            else:
                # Flat shape: {"name": ..., "arguments"/"args": ...}
                name = tc.get("name")
                arguments = tc.get("arguments", tc.get("args"))
            if not name:
                continue
            if arguments is None:
                arguments = "{}"
            tool_calls.append({
                "id": tc.get("id") or f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
        elif hasattr(tc, "function"):
            tool_calls.append({
                "id": getattr(tc, "id", ""),
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
    return tool_calls


def _accumulate_openai_stream_tool_calls(tool_call_chunks: dict) -> List[Dict[str, Any]]:
    """Reassemble streamed tool-call deltas into complete tool calls.
    
    Parses the accumulated JSON string arguments into proper objects.
    """
    import json
    result = []
    for idx in sorted(tool_call_chunks.keys()):
        tc = tool_call_chunks[idx]
        args_str = tc["args"]
        
        # Parse JSON string into object
        try:
            arguments = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            # If JSON parsing fails, keep as string (some providers may return non-JSON)
            arguments = args_str
        
        result.append({
            "id": tc["id"],
            "type": "function",
            "function": {"name": tc["name"], "arguments": arguments},
        })
    return result


# ---------------------------------------------------------------------------
# Text-embedded tool call recovery (provider-agnostic fallback)
# ---------------------------------------------------------------------------

_FENCED_BLOCK_RE = _re.compile(
    r"```[ \t]*([A-Za-z0-9_+\-]*)?[ \t]*(?:\r?\n)?(.*?)```", _re.DOTALL
)

# Argument-bearing keys, tried in order, for flat/dict tool-call shapes.
_ARGUMENT_KEYS = ("arguments", "args", "params", "parameters")
# Name-bearing keys, tried in order.
_NAME_KEYS = ("tool_name", "tool", "name")


def _coerce_tool_call(candidate: Any, available_tools: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """Turn one parsed JSON candidate into a normalized tool call dict.

    Accepted shapes (what models actually emit when they write the call
    instead of — or in addition to — using the native tool-call channel):

    - ``{"type": "function", "function": {"name": ..., "arguments": {...}}}``
    - ``{"name": ..., "arguments": {...}}``
    - ``{"tool_name": ..., "params"/"arguments": {...}}``

    Returns ``None`` when the candidate is not a tool call, or when its name
    is not in ``available_tools`` (guard against executing JSON the model only
    *mentioned* in prose/examples).
    """
    import json as _json

    if not isinstance(candidate, dict):
        return None

    func = candidate.get("function")
    if isinstance(func, dict):
        name = func.get("name")
        arguments = func.get("arguments")
    else:
        name = None
        for key in _NAME_KEYS:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        arguments = None
        for key in _ARGUMENT_KEYS:
            if key in candidate:
                arguments = candidate.get(key)
                break

    if not name or not isinstance(name, str):
        return None
    # Guard: only execute calls to tools that are actually registered.
    if available_tools is not None and name not in available_tools:
        return None

    if arguments is None:
        arguments = {}
    elif isinstance(arguments, str):
        try:
            parsed = _json.loads(arguments) if arguments.strip() else {}
        except (ValueError, TypeError):
            parsed = arguments  # keep raw; tool executor reports a clear error
        arguments = parsed
    if not isinstance(arguments, dict):
        # e.g. a JSON array/scalar — wrap so the executor still gets an object.
        arguments = {"input": arguments}

    return {
        "id": f"call_text_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def extract_tool_calls_from_text(
    content: Optional[str],
    available_tools: Optional[List[str]] = None,
    max_calls: int = 4,
) -> List[Dict[str, Any]]:
    """Recover tool calls a model wrote into its answer text.

    Used as a fallback by the orchestrator when the provider reported **no**
    native tool calls: models that can't (or don't) emit the native channel —
    or providers that drop them — print the call as a fenced JSON block, and
    without this the orchestrator treats that text as the final response, the
    turn ends and the user is asked for input again ("stops right when it made
    the tool call").

    Conservative by design so a JSON snippet the model merely *mentions* is
    never executed: only fenced ``` blocks (or a message that is a single JSON
    object) are considered, the payload must be a dict, and the tool name must
    be in ``available_tools`` when that is provided.
    """
    if not content or not content.strip():
        return []

    allowed = set(available_tools) if available_tools is not None else None
    candidates: List[Any] = []

    for match in _FENCED_BLOCK_RE.finditer(content):
        block = match.group(2).strip()
        if not block:
            continue
        try:
            candidates.append(_json_loads_object(block))
        except (ValueError, TypeError):
            continue

    # Whole message is a bare JSON object (no fence).
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            candidates.append(_json_loads_object(stripped))
        except (ValueError, TypeError):
            pass

    # Later blocks are the model's most recent intent → try them first.
    recovered: List[Dict[str, Any]] = []
    seen_names = set()
    for candidate in reversed(candidates):
        if isinstance(candidate, list):
            # A block holding a list of calls: {"calls": [...]} style payloads
            # are rare; keep it simple and only accept dicts of a single call.
            continue
        call = _coerce_tool_call(candidate, allowed)
        if call and call["function"]["name"] not in seen_names:
            recovered.append(call)
            seen_names.add(call["function"]["name"])
            if len(recovered) >= max_calls:
                break

    recovered.reverse()
    return recovered


def _json_loads_object(text: str) -> Any:
    """``json.loads`` with a small tolerance for prose around the object.

    Models often wrap the call ("Here is the call:\n```json ... ```"), so if a
    strict parse fails we retry on the outermost ``{...}`` region.
    """
    import json
    try:
        obj = json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        obj = json.loads(text[start:end + 1])
    if isinstance(obj, dict):
        return obj
    raise ValueError("not a JSON object")
