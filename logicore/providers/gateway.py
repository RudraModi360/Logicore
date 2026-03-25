"""
Provider Gateway Layer — The single entry point for all LLM API interactions.

Each gateway class owns the FULL lifecycle:
  1. Normalize OpenAI-style messages → SDK-specific format
  2. Normalize tool schemas → SDK-specific format
  3. Call the provider's SDK client directly
  4. Normalize the SDK response → NormalizedMessage { role, content, tool_calls }

Architecture:
  Agent (provider-agnostic)
        │
        │  chat() / chat_stream()
        ▼
  ProviderGateway
    OpenAIGateway   →  OpenAI / Groq  (OpenAI-compatible SDKs)
    GeminiGateway   →  Google Gemini  (google-genai SDK)
    AzureGateway    →  Azure AI       (OpenAI, Anthropic, Inference)
    OllamaGateway   →  Ollama         (ollama SDK, local models)
        │
        ▼
  NormalizedMessage { role, content, tool_calls }
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Tuple
import json
import inspect
import asyncio
import logging

logger = logging.getLogger(__name__)


def _gateway_debug(gateway: "ProviderGateway", message: str):
    """Emit gateway debug logs only when provider debug mode is enabled."""
    if getattr(gateway.provider, "debug", False):
        logger.debug(f"[Gateway:{gateway.provider_name}] {message}")


# ---------------------------------------------------------------------------
# Normalized output
# ---------------------------------------------------------------------------

class NormalizedMessage:
    """Standard message format returned by all gateways."""

    def __init__(self, role: str, content: str = "", tool_calls: List[Dict[str, Any]] = None,
                 name: str = None, tool_call_id: str = None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.name = name
        self.tool_call_id = tool_call_id

    def to_dict(self) -> Dict[str, Any]:
        result = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.name:
            result["name"] = self.name
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        return result


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

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
    ) -> NormalizedMessage:
        pass

    @abstractmethod
    async def chat_stream(
        self, messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
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


async def _dispatch_stream_text(on_token, text: str):
    """Emit streamed text progressively to ensure visible token-level updates."""
    if not text:
        return
    # Many providers send chunked blocks; split to per-char updates for smooth CLI streaming.
    for character in str(text):
        await _dispatch_token(on_token, character)


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
    """Reassemble streamed tool-call deltas into complete tool calls."""
    result = []
    for idx in sorted(tool_call_chunks.keys()):
        tc = tool_call_chunks[idx]
        result.append({
            "id": tc["id"],
            "type": "function",
            "function": {"name": tc["name"], "arguments": tc["args"]},
        })
    return result


# =========================================================================
# OpenAI-Compatible Gateway  (OpenAI, Groq)
# =========================================================================

class OpenAIGateway(ProviderGateway):
    """Gateway for OpenAI-compatible APIs (OpenAI, Groq)."""

    async def chat(self, messages, tools=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)

        kwargs = {"model": self.model_name, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        _gateway_debug(self, f"chat request: messages={len(messages)}, tools={len(tools) if tools else 0}")

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
            if "output text or tool calls" in error_msg:
                raise ValueError(f"Model returned empty response. Original: {e}")
            if "validation" in error_msg and "image" in error_msg:
                raise ValueError("Model does not support the given data type") from e
            if "must be a string" in error_msg and "content" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

    async def chat_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)

        kwargs = {"model": self.model_name, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        _gateway_debug(self, f"chat_stream request: messages={len(messages)}, tools={len(tools) if tools else 0}")

        try:
            stream = self.provider.client.chat.completions.create(**kwargs)
        except Exception as e:
            error_msg = str(e).lower()
            if "validation" in error_msg and "image" in error_msg:
                raise ValueError("Model does not support the given data type") from e
            if "must be a string" in error_msg and "content" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

        accumulated_content = ""
        tool_call_chunks = {}

        for chunk in stream:
            if not chunk or not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if hasattr(delta, "content") and delta.content:
                accumulated_content += delta.content
                await _dispatch_stream_text(on_token, delta.content)

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id and not tool_call_chunks[idx]["id"]:
                        tool_call_chunks[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_chunks[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_chunks[idx]["args"] += tc.function.arguments

        tool_calls = _accumulate_openai_stream_tool_calls(tool_call_chunks)
        if not accumulated_content and not tool_calls:
            raise ValueError("OpenAI-compatible model stream ended with no content or tool calls")
        _gateway_debug(self, f"chat_stream response: content_len={len(accumulated_content)}, tool_calls={len(tool_calls)}")
        return NormalizedMessage(
            role="assistant",
            content=accumulated_content,
            tool_calls=tool_calls,
        )


# =========================================================================
# Gemini Gateway
# =========================================================================

class GeminiGateway(ProviderGateway):
    """Gateway for Google Gemini API (google-genai SDK)."""

    def _sanitize_schema_for_gemini(self, schema: Any) -> Dict[str, Any]:
        """Normalize JSON schema to Gemini-compatible subset."""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}

        # Collapse combinators (anyOf/oneOf/allOf) to first viable branch.
        for comb_key in ("anyOf", "any_of", "oneOf", "one_of", "allOf", "all_of"):
            branches = schema.get(comb_key)
            if isinstance(branches, list) and branches:
                preferred = None
                for branch in branches:
                    if isinstance(branch, dict):
                        btype = branch.get("type")
                        if btype != "null":
                            preferred = branch
                            break
                preferred = preferred or (branches[0] if isinstance(branches[0], dict) else {})
                merged = dict(preferred)
                for key in ("description", "enum"):
                    if key in schema and key not in merged:
                        merged[key] = schema[key]
                return self._sanitize_schema_for_gemini(merged)

        sanitized: Dict[str, Any] = {}

        raw_type = schema.get("type")
        if isinstance(raw_type, list):
            non_null = [t for t in raw_type if t != "null"]
            raw_type = non_null[0] if non_null else raw_type[0]

        if isinstance(raw_type, str):
            sanitized["type"] = raw_type

        if isinstance(schema.get("description"), str):
            sanitized["description"] = schema["description"]

        if isinstance(schema.get("enum"), list):
            sanitized["enum"] = schema["enum"]

        if "properties" in schema and isinstance(schema["properties"], dict):
            sanitized["type"] = "object"
            sanitized["properties"] = {
                key: self._sanitize_schema_for_gemini(value)
                for key, value in schema["properties"].items()
                if isinstance(value, dict)
            }

        if "required" in schema and isinstance(schema["required"], list):
            sanitized["required"] = [r for r in schema["required"] if isinstance(r, str)]

        if "items" in schema:
            sanitized["type"] = "array"
            sanitized["items"] = self._sanitize_schema_for_gemini(schema.get("items", {}))

        # Ensure sane fallback for empty/unsupported schema fragments.
        if not sanitized:
            return {"type": "object", "properties": {}}

        # Gemini rejects additionalProperties in function declarations.
        sanitized.pop("additionalProperties", None)
        sanitized.pop("additional_properties", None)
        return sanitized

    def _build_contents(self, messages):
        """Convert OpenAI-style messages → Gemini Contents + system_instruction."""
        from logicore.providers.utils import extract_content
        from google.genai import types

        contents = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role")
            raw_content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            # --- system ---
            if role == "system":
                if isinstance(raw_content, str):
                    system_instruction = raw_content
                elif isinstance(raw_content, list):
                    texts = [p.get("text", "") for p in raw_content if p.get("type") == "text"]
                    system_instruction = " ".join(texts)
                continue

            parts = []

            # Regular content (text + images)
            if raw_content:
                text_content, images = extract_content(raw_content)
                if text_content:
                    parts.append(types.Part.from_text(text=text_content))
                for img in images:
                    if img["data"] and img["mime_type"]:
                        parts.append(types.Part.from_bytes(data=img["data"], mime_type=img["mime_type"]))

            # Assistant tool calls (outgoing history)
            if role == "assistant" and tool_calls:
                for tc in tool_calls:
                    func_data = tc.get("function", {})
                    name = func_data.get("name")
                    args_raw = func_data.get("arguments")
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except Exception:
                            args = {}
                    else:
                        args = args_raw or {}
                    parts.append(types.Part.from_function_call(name=name, args=args))

            # Tool results (incoming history)
            if role == "tool":
                name = msg.get("name")
                if not name and msg.get("tool_call_id"):
                    tid = msg["tool_call_id"]
                    if tid.startswith("call_"):
                        name = tid[5:]

                tool_content = msg.get("content")
                if isinstance(tool_content, str):
                    try:
                        resp_dict = json.loads(tool_content)
                        if not isinstance(resp_dict, dict):
                            resp_dict = {"result": resp_dict}
                    except Exception:
                        resp_dict = {"result": tool_content}
                else:
                    resp_dict = tool_content or {}

                contents.append(types.Content(
                    role="tool",
                    parts=[types.Part.from_function_response(name=name or "unknown_function", response=resp_dict)],
                ))
                continue

            # User / Assistant
            if role == "user" and parts:
                contents.append(types.Content(role="user", parts=parts))
            elif role == "assistant" and parts:
                contents.append(types.Content(role="model", parts=parts))

        return contents, system_instruction

    def _build_tools(self, tools):
        """Convert OpenAI-style tool schemas → Gemini FunctionDeclarations."""
        if not tools:
            return None
        from google.genai import types

        decls = []
        for t in tools:
            if t.get("type") == "function":
                func = t["function"]
                raw_parameters = func.get("parameters") or {"type": "object", "properties": {}}
                safe_parameters = self._sanitize_schema_for_gemini(raw_parameters)
                decls.append(types.FunctionDeclaration(
                    name=func["name"],
                    description=func.get("description", ""),
                    parameters=safe_parameters,
                ))
        return [types.Tool(function_declarations=decls)] if decls else None

    def _parse_response(self, response) -> NormalizedMessage:
        content = ""
        tool_calls = []

        parts = response.candidates[0].content.parts if response.candidates else []
        for part in parts:
            if part.text:
                content += part.text
            if part.function_call:
                fc = part.function_call
                tool_calls.append({
                    "id": f"call_{fc.name}",
                    "type": "function",
                    "function": {
                        "name": fc.name,
                        "arguments": json.dumps(fc.args) if fc.args else "{}",
                    },
                })

        if not content and not tool_calls:
            sdk_text = getattr(response, "text", None)
            if sdk_text:
                content = sdk_text
            else:
                raise ValueError("Gemini returned an empty response")

        return NormalizedMessage(role="assistant", content=content, tool_calls=tool_calls)

    async def chat(self, messages, tools=None) -> NormalizedMessage:
        from google.genai import types

        contents, system_instruction = self._build_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self._build_tools(tools),
        )

        try:
            response = self.provider.client.models.generate_content(
                model=self.model_name, contents=contents, config=config,
            )
            return self._parse_response(response)
        except Exception as e:
            error_msg = str(e)
            if "image" in error_msg.lower() and ("support" in error_msg.lower() or "type" in error_msg.lower()):
                if "tool" not in error_msg.lower():
                    raise ValueError(f"Gemini model '{self.model_name}' does not support this image/data type.") from e
            if "empty" in error_msg.lower() or "must contain" in error_msg.lower():
                raise ValueError(f"Gemini returned empty response. Original: {error_msg}")
            raise

    async def chat_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        from google.genai import types

        contents, system_instruction = self._build_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self._build_tools(tools),
        )

        try:
            content = ""
            tool_calls = []

            def get_stream():
                return self.provider.client.models.generate_content_stream(
                    model=self.model_name, contents=contents, config=config,
                )

            loop = asyncio.get_event_loop()
            stream = await loop.run_in_executor(None, get_stream)

            for chunk in stream:
                if chunk.text:
                    content += chunk.text
                    await _dispatch_stream_text(on_token, chunk.text)

                if chunk.candidates and chunk.candidates[0].content.parts:
                    for part in chunk.candidates[0].content.parts:
                        if part.function_call:
                            fc = part.function_call
                            tool_calls.append({
                                "id": f"call_{fc.name}",
                                "type": "function",
                                "function": {
                                    "name": fc.name,
                                    "arguments": json.dumps(fc.args) if fc.args else "{}",
                                },
                            })

            return NormalizedMessage(role="assistant", content=content, tool_calls=tool_calls)
        except Exception as e:
            raise


# =========================================================================
# Ollama Gateway
# =========================================================================

class OllamaGateway(ProviderGateway):
    """Gateway for Ollama (local models via ollama SDK)."""

    def _prepare_messages(
        self,
        messages,
        include_assistant_tool_calls: bool = True,
        coerce_tool_messages_to_user: bool = False,
    ):
        """Convert OpenAI-style messages → Ollama format.  Returns (filtered, has_images)."""
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

    async def chat(self, messages, tools=None) -> NormalizedMessage:
        filtered, has_images = self._prepare_messages(messages)
        if not filtered:
            raise ValueError("No valid messages to send to Ollama")

        sdk_tools = self._simplify_tools(tools, has_images)

        _gateway_debug(self, f"chat request: messages={len(filtered)}, tools={len(sdk_tools) if sdk_tools else 0}")

        try:
            response = self.provider.client.chat(
                model=self.model_name, messages=filtered, tools=sdk_tools,
            )

            if not response or "message" not in response:
                raise ValueError("Ollama returned invalid response structure")

            message = response["message"]
            if not message.get("content") and not message.get("tool_calls"):
                raise ValueError("Ollama returned empty message with no content or tool calls")

            return NormalizedMessage(
                role=message.get("role", "assistant"),
                content=message.get("content", ""),
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
                # Let 'must be a string' error fall through to the image error check
                if "must be a string" not in error_msg:
                    raise ValueError(f"Ollama error: {e}. Try a different model or check Ollama is running.")
            if ("support" in error_msg and "image" in error_msg) or "must be a string" in error_msg:
                raise ValueError("Model does not support image/media input. Please remove media from your request.") from e
            raise

    async def chat_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        filtered, has_images = self._prepare_messages(messages)
        if not filtered:
            raise ValueError("No valid messages to send to Ollama")

        sdk_tools = self._simplify_tools(tools, has_images)
        token_queue = asyncio.Queue()
        result_holder = {"message": None, "error": None}

        # Capture the event loop before spawning the thread
        loop = asyncio.get_event_loop()

        _gateway_debug(self, f"chat_stream request: messages={len(filtered)}, tools={len(sdk_tools) if sdk_tools else 0}")

        def sync_stream(stream_messages):
            full_content = ""
            tool_calls_result = []

            try:
                stream = self.provider.client.chat(
                    model=self.model_name, messages=stream_messages, tools=sdk_tools, stream=True,
                )

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
                            full_content += think_token
                            asyncio.run_coroutine_threadsafe(token_queue.put(think_token), loop)

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


# =========================================================================
# Azure Gateway
# =========================================================================

class AzureGateway(ProviderGateway):
    """Gateway for Azure AI (OpenAI, Anthropic, Inference backends)."""

    # --- Routing ---

    async def chat(self, messages, tools=None) -> NormalizedMessage:
        model_type = getattr(self.provider, "model_type", "openai")
        if model_type == "anthropic":
            return await self._chat_anthropic(messages, tools)
        else:
            return await self._chat_openai(messages, tools)

    async def chat_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        model_type = getattr(self.provider, "model_type", "openai")
        if model_type == "anthropic":
            return await self._chat_anthropic_stream(messages, tools, on_token)
        else:
            return await self._chat_openai_stream(messages, tools, on_token)

    # --- OpenAI-style (also used for Inference) ---

    async def _chat_openai(self, messages, tools=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)
        kwargs = {"model": deployment, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

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

    async def _chat_openai_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        messages = _convert_local_images_to_base64(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)
        kwargs = {"model": deployment, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools

        stream = self.provider.client.chat.completions.create(**kwargs)
        accumulated = ""
        tool_call_chunks = {}

        for chunk in stream:
            if not chunk or not hasattr(chunk, "choices") or not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if hasattr(delta, "content") and delta.content:
                accumulated += delta.content
                await _dispatch_stream_text(on_token, delta.content)

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id and not tool_call_chunks[idx]["id"]:
                        tool_call_chunks[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_chunks[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_chunks[idx]["args"] += tc.function.arguments

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

            tool_calls = []
            for tc in (msg.get("tool_calls") or []):
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
                })
            return NormalizedMessage(role="assistant", content=msg.get("content", ""), tool_calls=tool_calls)

    # --- Anthropic-style ---

    async def _chat_anthropic(self, messages, tools=None) -> NormalizedMessage:
        system_content, anthropic_msgs = self._format_for_anthropic(messages)
        kwargs = {"model": getattr(self.provider, "deployment_name", self.model_name),
                  "messages": anthropic_msgs, "max_tokens": 4096}
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

    async def _chat_anthropic_stream(self, messages, tools=None, on_token=None) -> NormalizedMessage:
        import queue, threading
        system_content, anthropic_msgs = self._format_for_anthropic(messages)
        deployment = getattr(self.provider, "deployment_name", self.model_name)

        kwargs = {"model": deployment, "messages": anthropic_msgs, "max_tokens": 4096, "system": system_content}
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
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    acc_tools.append({"id": event.content_block.id, "name": event.content_block.name, "args": ""})
                elif event.type == "content_block_delta" and hasattr(event.delta, "partial_json"):
                    if acc_tools:
                        acc_tools[-1]["args"] += event.delta.partial_json
            except queue.Empty:
                break

        tool_calls = [
            {"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["args"]}}
            for t in acc_tools
        ]
        return NormalizedMessage(role="assistant", content=acc_text, tool_calls=tool_calls)

    # --- Anthropic helpers ---

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


# =========================================================================
# Factory
# =========================================================================

def get_gateway_for_provider(provider) -> ProviderGateway:
    """Get the appropriate gateway for a provider instance."""
    provider_name = getattr(provider, "provider_name", "unknown").lower()

    GATEWAY_MAP = {
        "openai": OpenAIGateway,
        "groq": OpenAIGateway,  # Groq is OpenAI-compatible
        "gemini": GeminiGateway,
        "azure": AzureGateway,
        "ollama": OllamaGateway,
    }

    gateway_class = GATEWAY_MAP.get(provider_name, OpenAIGateway)
    return gateway_class(provider)
