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
)


# Transient empty-response retries (local Ollama occasionally returns an
# empty message under concurrent load; re-issuing usually succeeds).
OLLAMA_EMPTY_RETRY_ATTEMPTS = 5
OLLAMA_EMPTY_RETRY_BACKOFF = 0.5


class OllamaGateway(ProviderGateway):
    """Gateway for Ollama (local models via ollama SDK)."""

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
            if max_tokens:
                kwargs["options"] = {"num_predict": max_tokens}

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

            return NormalizedMessage(
                role=message.get("role", "assistant"),
                content=content,
                tool_calls=message.get("tool_calls", []),
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
                return NormalizedMessage(
                    role=message.get("role", "assistant"),
                    content=message.get("content", ""),
                    tool_calls=message.get("tool_calls", []),
                )
            if "empty" in error_msg or "invalid" in error_msg:
                if "must be a string" not in error_msg:
                    raise ValueError(f"Ollama error: {e}. Try a different model or check Ollama is running.")
            if ("support" in error_msg and "image" in error_msg) or "must be a string" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

    async def chat_stream(self, messages, tools=None, on_token=None, max_tokens=None) -> NormalizedMessage:
        filtered, has_images = self._prepare_messages(messages)
        if not filtered:
            raise ValueError("No valid messages to send to Ollama")

        sdk_tools = self._simplify_tools(tools, has_images)
        token_queue = asyncio.Queue()
        result_holder = {"message": None, "error": None}

        loop = asyncio.get_event_loop()

        _gateway_debug(self, f"chat_stream request: messages={len(filtered)}, tools={len(sdk_tools) if sdk_tools else 0}")

        def sync_stream(stream_messages):
            full_content = ""
            tool_calls_result = []

            try:
                kwargs = dict(model=self.model_name, messages=stream_messages, tools=sdk_tools, stream=True)
                if max_tokens:
                    kwargs["options"] = {"num_predict": max_tokens}
                stream = self.provider.client.chat(**kwargs)

                for chunk in stream:
                    msg = None
                    if isinstance(chunk, dict) and "message" in chunk:
                        msg = chunk["message"]
                    elif hasattr(chunk, "message"):
                        msg = chunk.message

                    if msg is not None:
                        token = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
                        if token:
                            full_content += token
                            asyncio.run_coroutine_threadsafe(token_queue.put(token), loop)

                        think_token = msg.get("thinking") if isinstance(msg, dict) else getattr(msg, "thinking", None)
                        if think_token:
                            pass

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
            except Exception as e:
                result_holder["error"] = e
            finally:
                asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(sync_stream, filtered)

        while True:
            token = await token_queue.get()
            if token is None:
                break
            await _dispatch_stream_text(on_token, token)

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
                return NormalizedMessage(
                    role=message.get("role", "assistant"),
                    content=message.get("content", ""),
                    tool_calls=message.get("tool_calls", []),
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
        )
