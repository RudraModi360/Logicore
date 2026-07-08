"""
Gemini gateway for Google's Gemini API (google-genai SDK).
"""

from typing import Any, Dict, List, Optional
import json
import asyncio

from .base import (
    ProviderGateway,
    NormalizedMessage,
    _gateway_debug,
    _dispatch_stream_text,
    _extract_cache_control,
    _strip_cache_annotations,
)


class GeminiGateway(ProviderGateway):
    """Gateway for Google Gemini API (google-genai SDK)."""

    # Gemini 3 / 2.5 "thinking" models attach a thought_signature to every
    # function-call part. That signature is cryptographically bound to the
    # model's hidden reasoning context, which is NOT exposed in the response
    # parts. Because this framework normalizes conversation history into an
    # OpenAI-style format, the exact original signature can never be replayed
    # faithfully (the API rejects any reconstruction as "corrupted"). The
    # official Gemini docs provide a documented escape hatch: a dummy signature
    # that tells the API to skip thought-signature validation.
    #   https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
    DUMMY_THOUGHT_SIGNATURE = b"skip_thought_signature_validator"

    def _sanitize_schema_for_gemini(self, schema: Any) -> Dict[str, Any]:
        """Normalize JSON schema to Gemini-compatible subset."""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}

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

        if not sanitized:
            return {"type": "object", "properties": {}}

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

            if role == "system":
                if isinstance(raw_content, str):
                    system_instruction = raw_content
                elif isinstance(raw_content, list):
                    texts = [p.get("text", "") for p in raw_content if p.get("type") == "text"]
                    system_instruction = " ".join(texts)
                continue

            # Replay assistant messages with a dummy thought signature so the
            # API skips its (impossible-to-satisfy) signature validation. Only
            # the first function-call part of a turn needs the signature; extra
            # signatures on parallel calls are rejected, so we attach it to the
            # first tool call only.
            parts = []

            if raw_content:
                text_content, images = extract_content(raw_content)
                if text_content:
                    parts.append(types.Part.from_text(text=text_content))
                for img in images:
                    if img["data"] and img["mime_type"]:
                        parts.append(types.Part.from_bytes(data=img["data"], mime_type=img["mime_type"]))

            if role == "assistant" and tool_calls:
                for idx, tc in enumerate(tool_calls):
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
                    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
                    if idx == 0:
                        part.thought_signature = self.DUMMY_THOUGHT_SIGNATURE
                    parts.append(part)

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

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
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

    async def chat(self, messages, tools=None, max_tokens=None) -> NormalizedMessage:
        from google.genai import types

        cache_control = _extract_cache_control(messages)
        messages = _strip_cache_annotations(messages)

        contents, system_instruction = self._build_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self._build_tools(tools),
        )
        if max_tokens:
            config.max_output_tokens = max_tokens

        _gateway_debug(self, f"chat request: messages={len(messages)}, tools={len(tools) if tools else 0}, cache_control={cache_control}")

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

    async def chat_stream(self, messages, tools=None, on_token=None, max_tokens=None) -> NormalizedMessage:
        from google.genai import types

        cache_control = _extract_cache_control(messages)
        messages = _strip_cache_annotations(messages)

        contents, system_instruction = self._build_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self._build_tools(tools),
        )
        if max_tokens:
            config.max_output_tokens = max_tokens

        try:
            content = ""
            tool_calls = []
            fc_index = {}

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

                chunk_parts = (
                    chunk.candidates[0].content.parts
                    if (chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts)
                    else []
                )
                for part in chunk_parts:
                    if part.function_call:
                        fc = part.function_call
                        cid = fc.id or f"call_{fc.name}"
                        if cid not in fc_index:
                            fc_index[cid] = len(tool_calls)
                            tool_calls.append({
                                "id": cid,
                                "type": "function",
                                "function": {
                                    "name": fc.name,
                                    "arguments": json.dumps(fc.args) if fc.args else "{}",
                                },
                            })
                        else:
                            idx = fc_index[cid]
                            try:
                                existing = json.loads(tool_calls[idx]["function"]["arguments"] or "{}")
                            except Exception:
                                existing = {}
                            if fc.args:
                                existing.update(fc.args)
                            tool_calls[idx]["function"]["arguments"] = json.dumps(existing)

            return NormalizedMessage(role="assistant", content=content, tool_calls=tool_calls)
        except Exception as e:
            raise
