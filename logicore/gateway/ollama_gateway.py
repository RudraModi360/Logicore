"""
Ollama gateway for local models via the ollama SDK.
"""

from typing import List, Dict, Any, Optional
import asyncio
import logging

from .base import (
    ProviderGateway,
    NormalizedMessage,
    _gateway_debug,
    _dispatch_stream_text,
    _dispatch_event,
)


# Transient empty-response retries (local Ollama occasionally returns an
# empty message under concurrent load; re-issuing usually succeeds).
OLLAMA_EMPTY_RETRY_ATTEMPTS = 5
OLLAMA_EMPTY_RETRY_BACKOFF = 0.5


class OllamaGateway(ProviderGateway):
    """Gateway for Ollama (local models via ollama SDK).
    
    Configuration options:
        think (bool): Enable thinking/reasoning in requests. Default: None (let model decide).
        treat_thinking_as_content (bool): If True, treat thinking tokens as normal content
            instead of reasoning events. Useful for models that don't properly separate
            thinking from content. Default: False.
    """

    def _prepare_messages(
        self,
        messages,
        include_assistant_tool_calls: bool = True,
        coerce_tool_messages_to_user: bool = False,
    ):
        """Convert OpenAI-style messages → Ollama format. Returns (filtered, has_images)."""
        from logicore.providers.utils import extract_content

        _gateway_debug(
            self,
            (
                "prepare_messages input: "
                f"count={len(messages)}, "
                f"include_assistant_tool_calls={include_assistant_tool_calls}, "
                f"coerce_tool_messages_to_user={coerce_tool_messages_to_user}"
            ),
        )

        filtered = []
        has_images = False

        for msg in messages:
            role = msg.get("role")
            raw_content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            if role == "tool" and coerce_tool_messages_to_user:
                tool_name = msg.get("name")
                tool_call_id = msg.get("tool_call_id", "")
                if not tool_name and isinstance(tool_call_id, str) and tool_call_id.startswith("call_"):
                    tool_name = tool_call_id[5:]
                role = "user"
                raw_content = f"Tool '{tool_name or 'unknown_tool'}' result: {raw_content}"

            text_content, images = extract_content(raw_content)
            ollama_msg = {"role": role, "content": text_content}

            if images:
                has_images = True
                import base64
                ollama_images = []
                for img in images:
                    if img.get("data"):
                        ollama_images.append(base64.b64encode(img["data"]).decode("utf-8"))
                if ollama_images:
                    ollama_msg["images"] = ollama_images

            if tool_calls:
                if role == "assistant" and not include_assistant_tool_calls:
                    tool_calls = None

                if isinstance(tool_calls, dict):
                    if "required" in tool_calls or "properties" in tool_calls:
                        tool_calls = None
                    else:
                        tool_calls = [tool_calls]

                if tool_calls:
                    clean_calls = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            if "required" in tc or "properties" in tc:
                                continue
                            clean_calls.append(tc)
                        elif hasattr(tc, "function"):
                            clean_calls.append({
                                "function": {
                                    "name": getattr(tc.function, "name", ""),
                                    "arguments": getattr(tc.function, "arguments", {}),
                                }
                            })
                    if clean_calls:
                        ollama_msg["tool_calls"] = clean_calls

            if role in ("assistant", "tool") and "name" in msg:
                ollama_msg["name"] = msg["name"]
            if role == "tool" and "tool_call_id" in msg:
                ollama_msg["tool_call_id"] = msg["tool_call_id"]

            if role and (text_content or images or tool_calls):
                filtered.append(ollama_msg)

        _gateway_debug(self, f"prepare_messages output: count={len(filtered)}, has_images={has_images}")
        return filtered, has_images

    def _simplify_tools(self, tools, has_images):
        """Simplify tool schemas for Ollama; disable when images present."""
        if has_images or not tools:
            return None
        from logicore.providers.utils import simplify_tool_schema
        return [simplify_tool_schema(t) for t in tools]

    async def chat(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        filtered, has_images = self._prepare_messages(messages)
        if not filtered:
            raise ValueError("No valid messages to send to Ollama")

        sdk_tools = self._simplify_tools(tools, has_images)

        _gateway_debug(self, f"chat request: messages={len(filtered)}, tools={len(sdk_tools) if sdk_tools else 0}")

        try:
            kwargs = dict(model=self.model_name, messages=filtered, tools=sdk_tools)
            options = {}
            if max_tokens:
                options["num_predict"] = max_tokens
            # Pass context window size to Ollama (default 4096 is too small for most agents)
            ctx = getattr(self, "context_window", None)
            if ctx is None:
                try:
                    from logicore.runtime.context.token_estimator import get_model_context_window
                    ctx = get_model_context_window(self.model_name)
                except Exception:
                    pass
            if ctx:
                options["num_ctx"] = int(ctx)
            if options:
                kwargs["options"] = options

            # Keep model resident for KV-cache reuse across turns
            kwargs["keep_alive"] = getattr(self, "keep_alive", -1)

            # Enable thinking if configured
            think = getattr(self, "think", None)
            if think is not None:
                kwargs["think"] = think

            # Retry a few times: local Ollama occasionally returns an empty
            # message under concurrent load, which is transient (not a real
            # failure). Re-issuing the request usually succeeds.
            last_err: Optional[Exception] = None
            response: Any = None
            for _attempt in range(OLLAMA_EMPTY_RETRY_ATTEMPTS):
                try:
                    response = self.provider.client.chat(**kwargs)
                    if response and "message" in response:
                        message = response["message"]
                        if message.get("content") or message.get("tool_calls"):
                            break
                    last_err = ValueError(
                        "Ollama returned empty message with no content or tool calls"
                    )
                except Exception as _e:  # noqa: BLE001
                    last_err = _e
                # brief backoff before retrying an empty/transient response
                if _attempt < OLLAMA_EMPTY_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(OLLAMA_EMPTY_RETRY_BACKOFF)

            if not response or "message" not in response:
                raise last_err or ValueError("Ollama returned invalid response structure")

            message = response["message"]
            if not message.get("content") and not message.get("tool_calls"):
                raise ValueError("Ollama returned empty message with no content or tool calls")

            thinking = message.get("thinking", "")
            content = message.get("content", "")

            if thinking and not content:
                _gateway_debug(self, f"Model returned thinking only ({len(thinking)} chars), no content")
                content = ""

            tcs = message.get("tool_calls") or []
            if tcs:
                names = [tc.get("function", {}).get("name") if isinstance(tc, dict) else getattr(tc, "function", None) for tc in tcs]
                _gateway_debug(
                    self,
                    f"Response: {len(content)} chars content, "
                    f"tool_calls={[n.get('name') if isinstance(n, dict) else n for n in names]}",
                )
            else:
                _gateway_debug(self, f"Response: {len(content)} chars content, no tool calls")

            usage = None
            prompt_eval = getattr(response, "prompt_eval_count", None)
            eval_count = getattr(response, "eval_count", None)
            _gateway_debug(self, f"Ollama response: prompt_eval_count={prompt_eval} eval_count={eval_count}")
            if prompt_eval is not None or eval_count is not None:
                usage = {}
                if prompt_eval is not None:
                    usage["prompt_tokens"] = prompt_eval
                if eval_count is not None:
                    usage["completion_tokens"] = eval_count
                if "prompt_tokens" in usage and "completion_tokens" in usage:
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            _gateway_debug(self, f"Ollama extracted usage: {usage}")

            return NormalizedMessage(
                role=message.get("role", "assistant"),
                content=content,
                tool_calls=message.get("tool_calls", []),
                usage=usage,
            )
        except Exception as e:
            error_msg = str(e).lower()
            if (
                "thought_signature" in error_msg
                or "function_response.name" in error_msg
                or "functioon_response.name" in error_msg
                or "name cannot be empty" in error_msg
            ):
                _gateway_debug(self, "gemini-style tool-history error detected, retrying with sanitized history")
                filtered_retry, _ = self._prepare_messages(
                    messages,
                    include_assistant_tool_calls=False,
                    coerce_tool_messages_to_user=True,
                )
                response = self.provider.client.chat(
                    model=self.model_name, messages=filtered_retry, tools=sdk_tools,
                )
                if not response or "message" not in response:
                    raise ValueError("Ollama retry returned invalid response structure")
                message = response["message"]
                if not message.get("content") and not message.get("tool_calls"):
                    raise ValueError("Ollama retry returned empty message with no content or tool calls")

                usage = None
                raw_usage = response.get("usage") or {}
                if raw_usage:
                    usage = {k: v for k, v in raw_usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                prompt_eval = response.get("prompt_eval_count")
                eval_count = response.get("eval_count")
                if prompt_eval is not None or eval_count is not None:
                    if usage is None:
                        usage = {}
                    if "prompt_tokens" not in usage and prompt_eval is not None:
                        usage["prompt_tokens"] = prompt_eval
                    if "completion_tokens" not in usage and eval_count is not None:
                        usage["completion_tokens"] = eval_count
                    if "prompt_tokens" in usage and "completion_tokens" in usage:
                        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

                return NormalizedMessage(
                    role=message.get("role", "assistant"),
                    content=message.get("content", ""),
                    tool_calls=message.get("tool_calls", []),
                    usage=usage,
                )
            if "empty" in error_msg or "invalid" in error_msg:
                if "must be a string" not in error_msg:
                    raise ValueError(f"Ollama error: {e}. Try a different model or check Ollama is running.")
            if ("support" in error_msg and "image" in error_msg) or "must be a string" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

    async def chat_stream(self, messages, tools=None, on_token=None, on_event=None, max_tokens=None) -> NormalizedMessage:
        filtered, has_images = self._prepare_messages(messages)
        if not filtered:
            raise ValueError("No valid messages to send to Ollama")

        sdk_tools = self._simplify_tools(tools, has_images)
        # Queue carries dicts: {"kind": "token"|"event", ...} or None sentinel.
        item_queue = asyncio.Queue()
        result_holder = {"message": None, "error": None, "usage": None}

        loop = asyncio.get_event_loop()
        
        # Get configuration options
        treat_thinking_as_content = getattr(self, "treat_thinking_as_content", False)
        think = getattr(self, "think", None)

        _gateway_debug(self, f"chat_stream request: messages={len(filtered)}, tools={len(sdk_tools) if sdk_tools else 0}, treat_thinking_as_content={treat_thinking_as_content}")

        def sync_stream(stream_messages):
            full_content = ""
            tool_calls_result = []
            stream_usage = None

            try:
                kwargs = dict(model=self.model_name, messages=stream_messages, tools=sdk_tools, stream=True)
                options = {}
                if max_tokens:
                    options["num_predict"] = max_tokens
                ctx = getattr(self, "context_window", None)
                if ctx is None:
                    try:
                        from logicore.runtime.context.token_estimator import get_model_context_window
                        ctx = get_model_context_window(self.model_name)
                    except Exception:
                        pass
                if ctx:
                    options["num_ctx"] = int(ctx)
                if options:
                    kwargs["options"] = options
                
                # Keep model resident for KV-cache reuse across turns
                kwargs["keep_alive"] = getattr(self, "keep_alive", -1)

                # Enable thinking if configured
                if think is not None:
                    kwargs["think"] = think
                    
                stream = self.provider.client.chat(**kwargs)

                for chunk in stream:
                    msg = None
                    if isinstance(chunk, dict) and "message" in chunk:
                        msg = chunk["message"]
                    elif hasattr(chunk, "message"):
                        msg = chunk.message

                    # Capture usage from stream chunks (Ollama provides this)
                    if isinstance(chunk, dict):
                        pe = chunk.get("prompt_eval_count")
                        ec = chunk.get("eval_count")
                    else:
                        pe = getattr(chunk, "prompt_eval_count", None)
                        ec = getattr(chunk, "eval_count", None)
                    if pe is not None or ec is not None:
                        stream_usage = {}
                        if pe is not None:
                            stream_usage["prompt_tokens"] = pe
                        if ec is not None:
                            stream_usage["completion_tokens"] = ec
                        if "prompt_tokens" in stream_usage and "completion_tokens" in stream_usage:
                            stream_usage["total_tokens"] = stream_usage["prompt_tokens"] + stream_usage["completion_tokens"]

                    if msg is not None:
                        token = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
                        if token:
                            full_content += token
                            asyncio.run_coroutine_threadsafe(item_queue.put({"kind": "token", "text": token}), loop)

                        # Reasoning / extended-thinking tokens.
                        # If treat_thinking_as_content is True, treat thinking tokens as normal content.
                        think_token = msg.get("thinking") if isinstance(msg, dict) else getattr(msg, "thinking", None)
                        if think_token:
                            if treat_thinking_as_content:
                                # Treat thinking as normal content (useful for models that don't separate thinking)
                                full_content += think_token
                                asyncio.run_coroutine_threadsafe(item_queue.put({"kind": "token", "text": think_token}), loop)
                            else:
                                # Emit as reasoning event (default behavior)
                                asyncio.run_coroutine_threadsafe(
                                    item_queue.put({"kind": "event", "event": {"type": "reasoning", "data": {"delta": think_token}}}),
                                    loop,
                                )

                        tc = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
                        if tc:
                            if isinstance(tc, list):
                                tool_calls_result.extend(tc)
                            else:
                                tool_calls_result.append(tc)

                final = {"role": "assistant", "content": full_content}
                if tool_calls_result:
                    final["tool_calls"] = tool_calls_result
                result_holder["message"] = final
                result_holder["usage"] = stream_usage
            except Exception as e:
                result_holder["error"] = e
            finally:
                asyncio.run_coroutine_threadsafe(item_queue.put(None), loop)

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(sync_stream, filtered)

        while True:
            item = await item_queue.get()
            if item is None:
                break
            if item["kind"] == "token":
                await _dispatch_stream_text(on_token, item["text"])
                await _dispatch_event(on_event, "token", {"delta": item["text"]})
            elif item["kind"] == "event":
                await _dispatch_event(on_event, item["event"]["type"], item["event"].get("data", {}))

        await asyncio.get_event_loop().run_in_executor(None, future.result)

        if result_holder["error"]:
            error_msg = str(result_holder["error"]).lower()
            if (
                "thought_signature" in error_msg
                or "function_response.name" in error_msg
                or "functioon_response.name" in error_msg
                or "name cannot be empty" in error_msg
            ):
                _gateway_debug(self, "gemini-style tool-history error detected in stream, retrying through sanitized non-stream path")
                executor.shutdown(wait=False)
                filtered_retry, _ = self._prepare_messages(
                    messages,
                    include_assistant_tool_calls=False,
                    coerce_tool_messages_to_user=True,
                )
                sdk_tools_retry = self._simplify_tools(tools, has_images)
                response = self.provider.client.chat(
                    model=self.model_name, messages=filtered_retry, tools=sdk_tools_retry,
                )
                if not response or "message" not in response:
                    raise ValueError("Ollama sanitized retry returned invalid response structure")
                message = response["message"]
                if not message.get("content") and not message.get("tool_calls"):
                    raise ValueError("Ollama sanitized retry returned empty message with no content or tool calls")
                retry_usage = None
                pe = response.get("prompt_eval_count")
                ec = response.get("eval_count")
                if pe is not None or ec is not None:
                    retry_usage = {}
                    if pe is not None:
                        retry_usage["prompt_tokens"] = pe
                    if ec is not None:
                        retry_usage["completion_tokens"] = ec
                    if "prompt_tokens" in retry_usage and "completion_tokens" in retry_usage:
                        retry_usage["total_tokens"] = retry_usage["prompt_tokens"] + retry_usage["completion_tokens"]
                return NormalizedMessage(
                    role=message.get("role", "assistant"),
                    content=message.get("content", ""),
                    tool_calls=message.get("tool_calls", []),
                    usage=retry_usage,
                )
            if ("support" in error_msg and "image" in error_msg) or "must be a string" in error_msg:
                executor.shutdown(wait=False)
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from result_holder["error"]
            if result_holder["error"]:
                executor.shutdown(wait=False)
                raise result_holder["error"]

        executor.shutdown(wait=False)

        msg = result_holder["message"]
        return NormalizedMessage(
            role=msg.get("role", "assistant"),
            content=msg.get("content", ""),
            tool_calls=msg.get("tool_calls", []),
            usage=result_holder.get("usage"),
        )
