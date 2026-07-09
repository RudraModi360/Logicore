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

logger = logging.getLogger(__name__)


def _gateway_debug(gateway: "ProviderGateway", message: str):
    """Emit gateway debug logs only when provider debug mode is enabled."""
    if getattr(gateway.provider, "debug", False):
        logger.debug(f"[Gateway:{gateway.provider_name}] {message}")


class NormalizedMessage:
    """Standard message format returned by all gateways."""

    def __init__(self, role: str, content: str = "", tool_calls: List[Dict[str, Any]] = None,
                 name: str = None, tool_call_id: str = None, extra: Dict[str, Any] = None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.name = name
        self.tool_call_id = tool_call_id
        self.extra = extra or {}

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
    except Exception:
        # Isolation: a failing consumer must not crash the provider stream.
        pass


async def _dispatch_stream_text(on_token, text: str):
    """Emit streamed text progressively to ensure visible token-level updates."""
    if not text:
        return
    await _dispatch_token(on_token, text)


def _extract_cache_control(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Extract cache control information from annotated messages.
    
    Returns cache control metadata for the last message with cache_control,
    or None if no cache control is present.
    """
    for msg in reversed(messages):
        cache_control = msg.get("_cache_control")
        if cache_control:
            return cache_control
    return None


def _strip_cache_annotations(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove cache control annotations from messages before sending to provider.
    
    Returns a new list with _cache_control keys removed.
    """
    cleaned = []
    for msg in messages:
        if "_cache_control" in msg:
            msg = {k: v for k, v in msg.items() if k != "_cache_control"}
        cleaned.append(msg)
    return cleaned


# Keys that belong to non-OpenAI providers (Anthropic, Gemini, ...).
# OpenAI-compatible APIs (OpenAI, Groq, etc.) reject them with a 400.
_OPENAI_UNSUPPORTED_MESSAGE_KEYS = ("tool_call_ids", "gemini_content", "_cache_control")


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
    """Convert SDK tool_calls objects to standard dicts."""
    tool_calls = []
    if raw_tool_calls:
        for tc in raw_tool_calls:
            if hasattr(tc, "function"):
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
