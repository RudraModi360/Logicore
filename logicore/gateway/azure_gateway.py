"""
Azure AI gateway supporting OpenAI, Anthropic, and Inference backends.
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
)


class AzureGateway(ProviderGateway):
    """Gateway for Azure AI (OpenAI, Anthropic, Inference backends)."""

    async def chat(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        model_type = getattr(self.provider, "model_type", "openai")
        if model_type == "anthropic":
            return await self._chat_anthropic(messages, tools, max_tokens)
        else:
            return await self._chat_openai(messages, tools, max_tokens)

    async def chat_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        model_type = getattr(self.provider, "model_type", "openai")
        if model_type == "anthropic":
            return await self._chat_anthropic_stream(messages, tools, on_token, on_event, max_tokens)
        else:
            return await self._chat_openai_stream(messages, tools, on_token, on_event, max_tokens)

    async def _chat_openai(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)
        kwargs = {"model": deployment, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        if self.provider.client:
            response = self.provider.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            normalized_tool_calls = _normalize_openai_tool_calls(getattr(msg, "tool_calls", None))
            content = getattr(msg, "content", "") or ""
            if not content and not normalized_tool_calls:
                raise ValueError("Azure OpenAI-compatible model returned empty response with no content or tool calls")
            return NormalizedMessage(
                role=getattr(msg, "role", "assistant"),
                content=content,
                tool_calls=normalized_tool_calls,
            )
        else:
            return await self._chat_inference_http(messages, tools)

    async def _chat_openai_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        import queue
        import threading
        
        messages = _convert_local_images_to_base64(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)
        kwargs = {"model": deployment, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        # Use thread-based streaming for reliability (OpenAI SDK sync streaming blocks event loop)
        q = queue.Queue()
        
        def worker():
            try:
                stream = self.provider.client.chat.completions.create(**kwargs)
                for chunk in stream:
                    q.put(("chunk", chunk))
                q.put(("done", None))
            except Exception as e:
                q.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

        accumulated = ""
        tool_call_chunks = {}

        while True:
            try:
                msg_type, data = q.get(timeout=60)
                if msg_type == "done":
                    break
                if msg_type == "error":
                    raise data

                chunk = data
                if not chunk or not hasattr(chunk, "choices") or not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if hasattr(delta, "content") and delta.content:
                    accumulated += delta.content
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
        if not accumulated and not tool_calls:
            raise ValueError("Azure OpenAI-compatible stream ended with no content or tool calls")

        return NormalizedMessage(
            role="assistant",
            content=accumulated,
            tool_calls=tool_calls,
        )

    async def _chat_inference_http(self, messages, tools=None) -> NormalizedMessage:
        """Fallback raw-HTTP path for Azure Inference / MaaS."""
        import httpx
        ep = getattr(self.provider, "endpoint", "")
        api_ver = getattr(self.provider, "api_version", "2024-05-01-preview")
        api_key = getattr(self.provider, "api_key", "")
        deployment = getattr(self.provider, "deployment_name", self.model_name)

        url = f"{ep}/chat/completions?api-version={api_ver}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {"messages": messages, "model": deployment}
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]

            tool_calls = _normalize_openai_tool_calls(msg.get("tool_calls"))
            return NormalizedMessage(role="assistant", content=msg.get("content", ""), tool_calls=tool_calls)

    async def _chat_anthropic(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        system_content, anthropic_msgs = self._format_for_anthropic(messages)
        kwargs = {"model": getattr(self.provider, "deployment_name", self.model_name),
                  "messages": anthropic_msgs, "max_tokens": max_tokens or 4096}
        if system_content:
            kwargs["system"] = system_content
        if tools:
            kwargs["tools"] = self._format_tools_anthropic(tools)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: self.provider.client.messages.create(**kwargs))

        content = "".join([b.text for b in response.content if hasattr(b, "text")])
        tool_calls = []
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_calls.append({
                    "id": block.id, "type": "function",
                    "function": {"name": block.name,
                                 "arguments": json.dumps(block.input) if isinstance(block.input, dict) else str(block.input)},
                })
        return NormalizedMessage(role="assistant", content=content, tool_calls=tool_calls)

    async def _chat_anthropic_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        import queue, threading
        system_content, anthropic_msgs = self._format_for_anthropic(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)

        kwargs = {"model": deployment, "messages": anthropic_msgs, "max_tokens": max_tokens or 4096, "system": system_content}
        if tools:
            kwargs["tools"] = self._format_tools_anthropic(tools)

        q = queue.Queue()

        def worker():
            try:
                with self.provider.client.messages.stream(**kwargs) as stream:
                    for event in stream:
                        q.put(("event", event))
                q.put(("done", None))
            except Exception as e:
                q.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

        acc_text = ""
        acc_tools = []

        while True:
            try:
                msg_type, data = q.get(timeout=60)
                if msg_type == "done":
                    break
                if msg_type == "error":
                    raise data

                event = data
                if event.type == "content_block_delta" and hasattr(event.delta, "text"):
                    acc_text += event.delta.text
                    await _dispatch_stream_text(on_token, event.delta.text)
                    await _dispatch_event(on_event, "token", {"delta": event.delta.text})
                elif event.type == "content_block_delta" and hasattr(event.delta, "thinking"):
                    await _dispatch_event(on_event, "reasoning", {"delta": event.delta.thinking})
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    acc_tools.append({"id": event.content_block.id, "name": event.content_block.name, "args": ""})
                    await _dispatch_event(
                        on_event, "tool_call_chunk",
                        {"call_id": event.content_block.id, "name": event.content_block.name, "args_delta": ""},
                    )
                elif event.type == "content_block_delta" and hasattr(event.delta, "partial_json"):
                    if acc_tools:
                        acc_tools[-1]["args"] += event.delta.partial_json
                        await _dispatch_event(
                            on_event, "tool_call_chunk",
                            {"call_id": acc_tools[-1]["id"], "name": acc_tools[-1]["name"], "args_delta": event.delta.partial_json},
                        )
            except queue.Empty:
                break

        tool_calls = []
        for t in acc_tools:
            # Parse JSON string arguments into objects
            try:
                arguments = json.loads(t["args"]) if t["args"] else {}
            except json.JSONDecodeError:
                arguments = t["args"]
            tool_calls.append({
                "id": t["id"],
                "type": "function",
                "function": {"name": t["name"], "arguments": arguments}
            })
        return NormalizedMessage(role="assistant", content=acc_text, tool_calls=tool_calls)

    def _format_for_anthropic(self, messages):
        from logicore.providers.utils import extract_content
        import base64

        system_content = None
        anthropic_msgs = []

        for msg in messages:
            if msg["role"] == "system":
                content = msg["content"]
                if isinstance(content, str):
                    system_content = [{"type": "text", "text": content}]
                elif isinstance(content, list):
                    system_content = content
                else:
                    system_content = [{"type": "text", "text": str(content)}]
            elif msg["role"] == "tool":
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}],
                })
            else:
                raw = msg.get("content", "")
                text, images = extract_content(raw)
                blocks = []
                for img in images:
                    data = img["data"]
                    if isinstance(data, bytes):
                        data = base64.b64encode(data).decode("utf-8")
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": img.get("mime_type", "image/png"), "data": data}})
                if text:
                    blocks.append({"type": "text", "text": text})
                anthropic_msgs.append({"role": msg["role"], "content": blocks or ""})

        return system_content, anthropic_msgs

    def _format_tools_anthropic(self, tools):
        return [
            {"name": t["function"]["name"], "description": t["function"].get("description"), "input_schema": t["function"].get("parameters")}
            for t in tools if "function" in t
        ]
