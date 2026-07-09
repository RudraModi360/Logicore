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
    _extract_cache_control,
    _strip_cache_annotations,
    _convert_local_images_to_base64,
    _normalize_openai_tool_calls,
    _accumulate_openai_stream_tool_calls,
    _strip_provider_specific_fields,
    _serialize_tool_call_arguments,
)


class OpenAIGateway(ProviderGateway):
    """Gateway for OpenAI-compatible APIs (OpenAI, Groq)."""

    async def chat(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        # Extract cache control info before stripping annotations
        cache_control = _extract_cache_control(messages)
        
        # Strip cache annotations before sending to provider
        messages = _strip_cache_annotations(messages)
        messages = _convert_local_images_to_base64(messages)
        messages = _strip_provider_specific_fields(messages)
        messages = _serialize_tool_call_arguments(messages)

        kwargs = {"model": self.model_name, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        _gateway_debug(self, f"chat request: messages={len(messages)}, tools={len(tools) if tools else 0}, cache_control={cache_control}")

        try:
            response = self.provider.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            normalized_tool_calls = _normalize_openai_tool_calls(getattr(msg, "tool_calls", None))
            content = getattr(msg, "content", "") or ""
            if not content and not normalized_tool_calls:
                raise ValueError("OpenAI-compatible model returned empty response with no content or tool calls")
            _gateway_debug(self, f"chat response: content_len={len(content)}, tool_calls={len(normalized_tool_calls)}")
            return NormalizedMessage(
                role=getattr(msg, "role", "assistant"),
                content=content,
                tool_calls=normalized_tool_calls,
            )
        except Exception as e:
            error_msg = str(e).lower()
            
            # Provide helpful context about what was sent
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

    async def chat_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        import queue
        import threading
        
        # Extract cache control info before stripping annotations
        cache_control = _extract_cache_control(messages)
        
        # Strip cache annotations before sending to provider
        messages = _strip_cache_annotations(messages)
        messages = _convert_local_images_to_base64(messages)
        messages = _strip_provider_specific_fields(messages)
        messages = _serialize_tool_call_arguments(messages)

        kwargs = {"model": self.model_name, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        _gateway_debug(self, f"chat_stream request: messages={len(messages)}, tools={len(tools) if tools else 0}, cache_control={cache_control}")

        # Use thread-based streaming for reliability (OpenAI SDK sync streaming blocks event loop)
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

                # Handle reasoning content (used by vLLM, DeepSeek, and other OpenAI-compatible providers)
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
        _gateway_debug(self, f"chat_stream response: content_len={len(accumulated_content)}, tool_calls={len(tool_calls)}")
        return NormalizedMessage(
            role="assistant",
            content=accumulated_content,
            tool_calls=tool_calls,
        )
