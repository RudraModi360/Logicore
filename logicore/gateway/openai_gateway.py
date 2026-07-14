"""
OpenAI-compatible gateway for providers like OpenAI and Groq.
"""

from typing import List, Dict, Any, Optional
import json
import asyncio

from .base import (
    ProviderGateway,
    NormalizedMessage,
    _gateway_debug,
    _dispatch_stream_text,
    _dispatch_event,
    _convert_local_images_to_base64,
    _normalize_openai_tool_calls,
    _accumulate_openai_stream_tool_calls,
    _strip_provider_specific_fields,
    _serialize_tool_call_arguments,
)


def _use_responses_api(provider) -> bool:
    """Check if this provider should use the Responses API (Groq returns cache info only via Responses)."""
    return getattr(provider, "provider_name", "") == "groq"


def _convert_messages_to_responses_input(messages: list) -> list:
    """Convert Chat Completions messages to Responses API input format."""
    input_items = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            # Responses API uses "developer" role for system instructions
            input_items.append({"role": "developer", "content": content})
        elif role in ("user", "assistant"):
            if isinstance(content, str):
                input_items.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Multi-part content (images, etc.)
                input_items.append({"role": role, "content": content})
            else:
                input_items.append({"role": role, "content": str(content)})
        # Skip tool messages and tool_call_id messages - they're handled differently in Responses API

    return input_items


def _convert_tools_to_responses_format(tools: list) -> list:
    """Convert Chat Completions tools to Responses API format.

    Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    if not tools:
        return []
    responses_tools = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            responses_tools.append({
                "type": "function",
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
            })
        else:
            responses_tools.append(tool)
    return responses_tools


def _parse_responses_output(output: list) -> tuple:
    """Parse Responses API output into (content, tool_calls, reasoning_text).

    Handles both Pydantic model objects (from SDK) and dicts (from mock/test).
    Returns:
        (content, tool_calls, reasoning_text)
    """
    content = ""
    reasoning_text = ""
    tool_calls = []

    def _get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    for item in output:
        item_type = _get(item, "type", "")

        if item_type == "reasoning":
            reasoning_content = _get(item, "content", None)
            if reasoning_content:
                for part in reasoning_content:
                    text = _get(part, "text", None)
                    if text:
                        reasoning_text += text

        elif item_type == "message":
            msg_content = _get(item, "content", None)
            if msg_content:
                for part in msg_content:
                    text = _get(part, "text", None)
                    if text:
                        content += text

        elif item_type == "function_call":
            name = _get(item, "name", "")
            arguments = _get(item, "arguments", "")
            call_id = _get(item, "call_id", "")
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            })

    return content, tool_calls, reasoning_text


def _extract_responses_usage(response) -> dict:
    """Extract usage dict from Responses API response."""
    usage_obj = getattr(response, "usage", None)
    if not usage_obj:
        return None

    usage = {}
    for attr in ("input_tokens", "output_tokens", "total_tokens"):
        val = getattr(usage_obj, attr, None)
        if val is not None:
            usage[attr] = val

    # Map to Chat Completions naming for canonical normalizer
    if "input_tokens" in usage:
        usage["prompt_tokens"] = usage.pop("input_tokens")
    if "output_tokens" in usage:
        usage["completion_tokens"] = usage.pop("output_tokens")

    details = getattr(usage_obj, "input_tokens_details", None)
    if details:
        pd = {}
        cached = getattr(details, "cached_tokens", None)
        if cached is not None:
            pd["cached_tokens"] = cached
        if pd:
            usage["prompt_tokens_details"] = pd

    out_details = getattr(usage_obj, "output_tokens_details", None)
    if out_details:
        od = {}
        odr = getattr(out_details, "reasoning_tokens", None)
        if odr is not None:
            od["reasoning_tokens"] = odr
        if od:
            usage["output_tokens_details"] = od

    return usage if usage else None


class OpenAIGateway(ProviderGateway):
    """Gateway for OpenAI-compatible APIs (OpenAI, Groq)."""

    async def chat(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)
        messages = _strip_provider_specific_fields(messages)
        messages = _serialize_tool_call_arguments(messages)

        if _use_responses_api(self.provider):
            return await self._chat_responses(messages, tools, max_tokens)

        return await self._chat_completions(messages, tools, max_tokens)

    async def _chat_completions(self, messages, tools, max_tokens) -> NormalizedMessage:
        kwargs = {"model": self.model_name, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        _gateway_debug(self, f"chat request: messages={len(messages)}, tools={len(tools) if tools else 0}")

        try:
            response = self.provider.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            normalized_tool_calls = _normalize_openai_tool_calls(getattr(msg, "tool_calls", None))
            content = getattr(msg, "content", "") or ""
            if not content and not normalized_tool_calls:
                raise ValueError("OpenAI-compatible model returned empty response with no content or tool calls")

            usage = None
            if hasattr(response, "usage") and response.usage:
                usage = {}
                for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    val = getattr(response.usage, attr, None)
                    if val is not None:
                        usage[attr] = val
                details = getattr(response.usage, "prompt_tokens_details", None)
                if details:
                    pd = {}
                    for dattr in ("cached_tokens", "cache_write_tokens", "reasoning_tokens"):
                        dval = getattr(details, dattr, None)
                        if dval is not None:
                            pd[dattr] = dval
                    if pd:
                        usage["prompt_tokens_details"] = pd
                out_details = getattr(response.usage, "output_tokens_details", None)
                if not out_details:
                    out_details = getattr(response.usage, "completion_tokens_details", None)
                if out_details:
                    od = {}
                    odr = getattr(out_details, "reasoning_tokens", None)
                    if odr is not None:
                        od["reasoning_tokens"] = odr
                    if od:
                        usage["output_tokens_details"] = od

            _gateway_debug(self, f"chat response: content_len={len(content)}, tool_calls={len(normalized_tool_calls)}")
            if usage:
                _gateway_debug(self, f"chat usage: {usage}")
            return NormalizedMessage(
                role=getattr(msg, "role", "assistant"),
                content=content,
                tool_calls=normalized_tool_calls,
                usage=usage,
            )
        except Exception as e:
            error_msg = str(e).lower()

            provider_endpoint = getattr(self.provider, 'endpoint', 'default')
            provider_model = getattr(self.provider, 'model_name', 'unknown')

            if "400" in str(e) or "upstream" in error_msg:
                raise ValueError(
                    f"Provider returned 400 error. "
                    f"Endpoint: {provider_endpoint}, Model: {provider_model}. "
                    f"Check if model name is correct for this endpoint. "
                    f"Original: {e}"
                ) from e
            if "output text or tool calls" in error_msg:
                raise ValueError(f"Model returned empty response. Original: {e}")
            if "validation" in error_msg and "image" in error_msg:
                raise ValueError("Model does not support the given data type") from e
            if "must be a string" in error_msg and "content" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

    async def _chat_responses(self, messages, tools, max_tokens) -> NormalizedMessage:
        """Non-streaming call via Groq Responses API (returns cache info)."""
        input_items = _convert_messages_to_responses_input(messages)
        kwargs = {"model": self.model_name, "input": input_items}
        if tools:
            kwargs["tools"] = _convert_tools_to_responses_format(tools)
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens

        _gateway_debug(self, f"responses request: input={len(input_items)}, tools={len(tools) if tools else 0}")

        try:
            response = self.provider._responses_client.responses.create(**kwargs)
            output = getattr(response, "output", []) or []
            content, tool_calls, reasoning_text = _parse_responses_output(output)

            _gateway_debug(self, f"responses raw: output_items={len(output)}, content='{content[:50]}', tool_calls={len(tool_calls)}, reasoning={len(reasoning_text)}")

            if not content and not tool_calls:
                raise ValueError("Groq Responses API returned empty response with no content or tool calls")

            usage = _extract_responses_usage(response)

            _gateway_debug(self, f"responses response: content_len={len(content)}, tool_calls={len(tool_calls)}")
            if usage:
                _gateway_debug(self, f"responses usage: {usage}")
            return NormalizedMessage(
                role="assistant",
                content=content,
                tool_calls=tool_calls,
                usage=usage,
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "400" in str(e):
                raise ValueError(f"Groq Responses API error: {e}") from e
            if "output text or tool calls" in error_msg:
                raise ValueError(f"Model returned empty response. Original: {e}")
            raise

    async def chat_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        import queue
        import threading

        messages = _convert_local_images_to_base64(messages)
        messages = _strip_provider_specific_fields(messages)
        messages = _serialize_tool_call_arguments(messages)

        if _use_responses_api(self.provider):
            return await self._chat_stream_responses(messages, tools, on_token, on_event, max_tokens)

        return await self._chat_stream_completions(messages, tools, on_token, on_event, max_tokens)

    async def _chat_stream_completions(self, messages, tools, on_token, on_event, max_tokens) -> NormalizedMessage:
        kwargs = {"model": self.model_name, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        _gateway_debug(self, f"chat_stream request: messages={len(messages)}, tools={len(tools) if tools else 0}")

        q = queue.Queue()

        def worker():
            try:
                stream = self.provider.client.chat.completions.create(**kwargs)
                for chunk in stream:
                    q.put(("chunk", chunk))
                q.put(("done", None))
            except Exception as e:
                _gateway_debug(self, f"Stream error: {e}")
                q.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

        accumulated_content = ""
        tool_call_chunks = {}

        while True:
            try:
                msg_type, data = q.get(timeout=60)
                if msg_type == "done":
                    break
                if msg_type == "error":
                    raise data

                chunk = data
                if not chunk or not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    await _dispatch_event(on_event, "reasoning", {"delta": delta.reasoning_content})

                if hasattr(delta, "content") and delta.content:
                    accumulated_content += delta.content
                    await _dispatch_stream_text(on_token, delta.content)
                    await _dispatch_event(on_event, "token", {"delta": delta.content})

                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_chunks:
                            tool_call_chunks[idx] = {"id": "", "name": "", "args": ""}
                        if tc.id and not tool_call_chunks[idx]["id"]:
                            tool_call_chunks[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                if not tool_call_chunks[idx]["name"]:
                                    await _dispatch_event(
                                        on_event, "tool_call_chunk",
                                        {"call_id": tc.id, "name": tc.function.name, "args_delta": ""},
                                    )
                                tool_call_chunks[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_call_chunks[idx]["args"] += tc.function.arguments
                                await _dispatch_event(
                                    on_event, "tool_call_chunk",
                                    {"call_id": tc.id, "name": tool_call_chunks[idx]["name"], "args_delta": tc.function.arguments},
                                )

            except queue.Empty:
                break

        tool_calls = _accumulate_openai_stream_tool_calls(tool_call_chunks)
        if not accumulated_content and not tool_calls:
            raise ValueError("OpenAI-compatible model stream ended with no content or tool calls")

        usage = None
        try:
            final_usage = getattr(stream, "usage", None) if "stream" in dir() else None
            if final_usage and hasattr(final_usage, "prompt_tokens"):
                usage = {}
                for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    val = getattr(final_usage, attr, None)
                    if val is not None:
                        usage[attr] = val
                details = getattr(final_usage, "prompt_tokens_details", None)
                if details:
                    pd = {}
                    for dattr in ("cached_tokens", "cache_write_tokens", "reasoning_tokens"):
                        dval = getattr(details, dattr, None)
                        if dval is not None:
                            pd[dattr] = dval
                    if pd:
                        usage["prompt_tokens_details"] = pd
                out_details = getattr(final_usage, "output_tokens_details", None)
                if not out_details:
                    out_details = getattr(final_usage, "completion_tokens_details", None)
                if out_details:
                    od = {}
                    odr = getattr(out_details, "reasoning_tokens", None)
                    if odr is not None:
                        od["reasoning_tokens"] = odr
                    if od:
                        usage["output_tokens_details"] = od
                _gateway_debug(self, f"chat_stream usage: {usage}")
        except Exception:
            pass

        _gateway_debug(self, f"chat_stream response: content_len={len(accumulated_content)}, tool_calls={len(tool_calls)}")
        return NormalizedMessage(
            role="assistant",
            content=accumulated_content,
            tool_calls=tool_calls,
            usage=usage,
        )

    async def _chat_stream_responses(self, messages, tools, on_token, on_event, max_tokens) -> NormalizedMessage:
        """Streaming call via Groq Responses API (returns cache info in response.completed event)."""
        import queue
        import threading

        input_items = _convert_messages_to_responses_input(messages)
        kwargs = {"model": self.model_name, "input": input_items, "stream": True}
        if tools:
            kwargs["tools"] = _convert_tools_to_responses_format(tools)
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens

        _gateway_debug(self, f"responses_stream request: input={len(input_items)}, tools={len(tools) if tools else 0}")

        q = queue.Queue()

        def worker():
            try:
                stream = self.provider._responses_client.responses.create(**kwargs)
                for event in stream:
                    q.put(("event", event))
                q.put(("done", None))
            except Exception as e:
                _gateway_debug(self, f"Responses stream error: {type(e).__name__}: {e}")
                q.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

        accumulated_content = ""
        accumulated_reasoning = ""
        # Track tool call items by their ID
        tool_call_items = {}  # call_id -> {"id": str, "name": str, "args": str}
        final_usage = None

        while True:
            try:
                msg_type, data = q.get(timeout=60)
                if msg_type == "done":
                    break
                if msg_type == "error":
                    raise data

                event = data
                ev = event.model_dump() if hasattr(event, "model_dump") else event
                event_type = ev.get("type", "")

                if event_type == "response.output_text.delta":
                    delta = ev.get("delta", "")
                    if delta:
                        accumulated_content += delta
                        await _dispatch_stream_text(on_token, delta)
                        await _dispatch_event(on_event, "token", {"delta": delta})

                elif event_type == "response.reasoning_text.delta":
                    delta = ev.get("delta", "")
                    if delta:
                        accumulated_reasoning += delta
                        await _dispatch_event(on_event, "reasoning", {"delta": delta})

                elif event_type == "response.function_call_arguments.delta":
                    delta = ev.get("delta", "")
                    item_id = ev.get("item_id", "")
                    if delta and item_id:
                        if item_id not in tool_call_items:
                            tool_call_items[item_id] = {"id": item_id, "name": "", "args": ""}
                        tool_call_items[item_id]["args"] += delta

                elif event_type == "response.output_item.added":
                    item = ev.get("item", {})
                    if item.get("type") == "function_call":
                        item_id = item.get("id", "")
                        name = item.get("name", "")
                        if item_id:
                            tool_call_items[item_id] = {"id": item_id, "name": name, "args": ""}
                            await _dispatch_event(
                                on_event, "tool_call_chunk",
                                {"call_id": item_id, "name": name, "args_delta": ""},
                            )

                elif event_type == "response.completed":
                    resp = ev.get("response", {})
                    final_usage = resp.get("usage")

            except queue.Empty:
                break

        tool_calls = []
        for tc in tool_call_items.values():
            if tc["name"]:
                tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["args"],
                    },
                })

        if not accumulated_content and not tool_calls:
            raise ValueError("Groq Responses API stream ended with no content or tool calls")

        usage = None
        if final_usage:
            usage = {}
            for attr in ("input_tokens", "output_tokens", "total_tokens"):
                val = final_usage.get(attr)
                if val is not None:
                    usage[attr] = val
            # Map to Chat Completions naming for canonical normalizer
            if "input_tokens" in usage:
                usage["prompt_tokens"] = usage.pop("input_tokens")
            if "output_tokens" in usage:
                usage["completion_tokens"] = usage.pop("output_tokens")

            details = final_usage.get("input_tokens_details")
            if details:
                pd = {}
                cached = details.get("cached_tokens")
                if cached is not None:
                    pd["cached_tokens"] = cached
                if pd:
                    usage["prompt_tokens_details"] = pd

            out_details = final_usage.get("output_tokens_details")
            if out_details:
                od = {}
                odr = out_details.get("reasoning_tokens")
                if odr is not None:
                    od["reasoning_tokens"] = odr
                if od:
                    usage["output_tokens_details"] = od

            _gateway_debug(self, f"responses_stream usage: {usage}")

        _gateway_debug(self, f"responses_stream response: content_len={len(accumulated_content)}, tool_calls={len(tool_calls)}")
        return NormalizedMessage(
            role="assistant",
            content=accumulated_content,
            tool_calls=tool_calls,
            usage=usage,
        )
