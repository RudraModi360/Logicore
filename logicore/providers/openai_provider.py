"""
OpenAI Provider - Direct OpenAI API wrapper.

Uses the official OpenAI SDK for standard OpenAI API endpoints.
Supports tool calling and streaming.
"""

import os
import inspect
import asyncio
from typing import List, Dict, Any, Optional, Callable
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    provider_name = "openai"
    
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")
        
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set api_key or OPENAI_API_KEY env var.")
        
        self.client = OpenAI(api_key=self.api_key, **kwargs)

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """Standard non-streaming chat via OpenAI API."""
        kwargs = {
            "model": self.model_name,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message
        except Exception as e:
            error_msg = str(e)
            if "output text or tool calls" in error_msg.lower():
                raise ValueError(
                    f"OpenAI model returned empty response. Original error: {error_msg}"
                )
            raise

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """Streaming chat via OpenAI API."""
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = self.client.chat.completions.create(**kwargs)

        accumulated_content = ""
        tool_call_chunks: Dict[int, Dict[str, str]] = {}

        for chunk in stream:
            if not chunk or not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                accumulated_content += delta.content
                if on_token:
                    if inspect.iscoroutinefunction(on_token):
                        await on_token(delta.content)
                    else:
                        on_token(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_call_chunks[idx]["id"] += tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_chunks[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_chunks[idx]["args"] += tc.function.arguments

        # Build final message object matching OpenAI SDK format
        from openai.types.chat import ChatCompletionMessage
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall,
            Function,
        )

        final_tool_calls = None
        if tool_call_chunks:
            final_tool_calls = [
                ChatCompletionMessageToolCall(
                    id=tc["id"],
                    type="function",
                    function=Function(name=tc["name"], arguments=tc["args"]),
                )
                for tc in [tool_call_chunks[i] for i in sorted(tool_call_chunks.keys())]
            ]

        return ChatCompletionMessage(
            role="assistant",
            content=accumulated_content or None,
            tool_calls=final_tool_calls,
        )

    def get_model_name(self) -> str:
        return self.model_name
