import os
import base64
import re
import asyncio
import json
import logging
from typing import List, Dict, Any, Optional, Union
from .base import LLMProvider

logger = logging.getLogger("logicore.providers.azure")

class ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = type('obj', (object,), {"name": name, "arguments": arguments})
        self.type = "function"

class MockMessage:
    def __init__(self, content: str, role: str = "assistant", tool_calls=None):
        self.content = content
        self.role = role
        self.tool_calls = tool_calls

class AzureProvider(LLMProvider):
    """A general wrapper for Azure AI LLM deployments.
    Supports:
    1. Azure OpenAI (GPT models)
    2. Azure AI Foundry - Anthropic (Claude models)
    3. Azure AI Inference (Llama, Mistral, Phi models via MaaS)
    
    The provider automatically detects the configuration guide to use based on the 
    endpoint and deployment name, allowing a single unified interface.
    """
    provider_name = "azure"
    
    MODEL_TYPE_OPENAI = "openai"
    MODEL_TYPE_ANTHROPIC = "anthropic"
    MODEL_TYPE_INFERENCE = "inference"  # Unified AI Inference API

    def __init__(
        self, 
        model_name: str,  # The deployment name in Azure
        api_key: Optional[str] = None, 
        endpoint: Optional[str] = None, 
        api_version: Optional[str] = None,
        model_type: Optional[str] = None,  # "openai", "anthropic", or "inference"
        **kwargs
    ):
        """
        Initialize the general Azure LLM wrapper.
        
        Args:
            model_name: Deployment name on Azure.
            api_key: Azure API key.
            endpoint: Azure endpoint URL.
            api_version: Optional API version (default varies by type).
            model_type: Optional explicit model type.
        """
        self.deployment_name = model_name
        # Fallback to multiple common env vars
        self.api_key = api_key or os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
        self.endpoint = (endpoint or os.environ.get("AZURE_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        self.kwargs = kwargs
        
        if not self.api_key:
            raise ValueError("Azure API key is required. Provide api_key or set AZURE_API_KEY env var.")
        if not self.endpoint:
            raise ValueError("Azure Endpoint is required. Provide endpoint or set AZURE_ENDPOINT env var.")

        # 1. Detect Model Type
        self.model_type = self._detect_model_type(model_type, model_name, self.endpoint)
        
        # 2. Set Default API Version if not provided
        self.api_version = api_version or self._get_default_api_version()
        
        # 3. Initialize the appropriate client
        self.client = None
        self._init_client()
        
        logger.info(f"Initialized Azure {self.model_type} provider for deployment: {self.deployment_name}")

    def _detect_model_type(self, explicit_type: Optional[str], model_name: str, endpoint: str) -> str:
        """Heuristic-based detection of Azure deployment type."""
        if explicit_type:
            return explicit_type.lower()
            
        endpoint_lower = endpoint.lower()
        model_name_lower = model_name.lower()
        
        # Check for MaaS / OpenAI Compatible endpoints first (often end in /v1)
        if "/v1" in endpoint_lower and "openai.azure.com" not in endpoint_lower:
             return self.MODEL_TYPE_INFERENCE
        
        if "anthropic" in endpoint_lower or "claude" in model_name_lower:
            return self.MODEL_TYPE_ANTHROPIC
        elif "openai.azure.com" in endpoint_lower or "gpt" in model_name_lower:
            return self.MODEL_TYPE_OPENAI
        elif "/models" in endpoint_lower or "inference" in endpoint_lower:
            return self.MODEL_TYPE_INFERENCE
        
        # Default to OpenAI if ambiguous (most common)
        return self.MODEL_TYPE_OPENAI

    def _get_default_api_version(self) -> str:
        if self.model_type == self.MODEL_TYPE_OPENAI:
            return "2024-10-21"
        elif self.model_type == self.MODEL_TYPE_ANTHROPIC:
            return "2023-06-01"
        return "2024-05-01-preview"

    def _init_client(self):
        """Initializes the underlying SDK client based on self.model_type."""
        if self.model_type == self.MODEL_TYPE_ANTHROPIC:
            try:
                from anthropic import AnthropicFoundry
                # Clean base URL for Anthropic
                # If user provided full path .../anthropic/v1/messages, strip it back
                base_url = self.endpoint
                if "/v1" in base_url:
                    base_url = base_url.split("/v1")[0]
                if "/anthropic" in base_url:
                    pass
                else:
                    pass
                
                # Ensure no trailing slash
                base_url = base_url.rstrip("/")
                
                self.client = AnthropicFoundry(api_key=self.api_key, base_url=base_url)
            except ImportError:
                logger.warning("Anthropic SDK not installed. Claude deployments may fail.")
                
        elif self.model_type == self.MODEL_TYPE_OPENAI:
            try:
                from openai import AzureOpenAI
                # Standard Azure OpenAI requires stripping paths
                base_url = self.endpoint
                if "/openai/" in base_url:
                    base_url = base_url.split("/openai/")[0]
                    
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=base_url
                )
            except ImportError:
                logger.warning("OpenAI SDK not installed. OpenAI deployments may fail.")
        
        elif self.model_type == self.MODEL_TYPE_INFERENCE:
            try:
                from openai import AzureOpenAI
                # For Inference/MaaS, use azure_endpoint but strip /openai/v1 if present
                # The SDK appends the necessary paths
                base_ep = self.endpoint
                if "/openai/v1" in base_ep:
                    base_ep = base_ep.split("/openai/v1")[0]
                
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=base_ep
                )
            except ImportError:
                logger.warning("OpenAI SDK not installed. Inference deployments may fail.")

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """Unified chat call."""
        if self.model_type == self.MODEL_TYPE_ANTHROPIC:
            return await self._chat_anthropic(messages, tools)
        elif self.model_type == self.MODEL_TYPE_INFERENCE:
            return await self._chat_inference(messages, tools)
        else:
            return await self._chat_openai(messages, tools)

    async def chat_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, on_token: Optional[Any] = None) -> Any:
        """Unified streaming chat call."""
        if self.model_type == self.MODEL_TYPE_ANTHROPIC:
            return await self._chat_anthropic_stream(messages, tools, on_token)
        else:
            # Default to OpenAI-style streaming for both OpenAI and Inference
            return await self._chat_openai_stream(messages, tools, on_token)

    # --- OpenAI Implementation ---
    
    async def _chat_openai(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        processed_msgs = self._format_messages_for_openai(messages)
        kwargs = {"model": self.deployment_name, "messages": processed_msgs}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message

    async def _chat_openai_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, on_token: Optional[Any] = None) -> Any:
        processed_msgs = self._format_messages_for_openai(messages)
        kwargs = {"model": self.deployment_name, "messages": processed_msgs, "stream": True}
        if tools:
            kwargs["tools"] = tools

        stream = self.client.chat.completions.create(**kwargs)
        accumulated_content = ""
        tool_call_chunks = {}

        for chunk in stream:
            if not chunk or not hasattr(chunk, 'choices') or not chunk.choices: continue
            delta = chunk.choices[0].delta
            
            if hasattr(delta, 'content') and delta.content:
                accumulated_content += delta.content
                if on_token:
                    import inspect
                    if inspect.iscoroutinefunction(on_token): await on_token(delta.content)
                    else: on_token(delta.content)
            
            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id: tool_call_chunks[idx]["id"] += tc.id
                    if tc.function:
                        if tc.function.name: tool_call_chunks[idx]["name"] += tc.function.name
                        if tc.function.arguments: tool_call_chunks[idx]["args"] += tc.function.arguments

        final_tool_calls = []
        for idx in sorted(tool_call_chunks.keys()):
            chunk = tool_call_chunks[idx]
            final_tool_calls.append(ToolCall(chunk["id"], chunk["name"], chunk["args"]))
            
        return MockMessage(content=accumulated_content, role="assistant", tool_calls=final_tool_calls or None)

    def _format_messages_for_openai(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize messages for OpenAI API, handling vision etc."""
        formatted = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if part.get("type") == "image":
                        data = part.get("data", "")
                        url = data if data.startswith("data:") else f"data:{part.get('mime_type','image/png')};base64,{data}"
                        new_content.append({"type": "image_url", "image_url": {"url": url}})
                    else:
                        new_content.append(part)
                formatted.append({**msg, "content": new_content})
            else:
                formatted.append(msg)
        return formatted

    # --- Anthropic Implementation ---

    async def _chat_anthropic(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        from .utils import extract_content
        system_msg, anthropic_msgs = self._format_messages_for_anthropic(messages)
        
        kwargs = {
            "model": self.deployment_name,
            "messages": anthropic_msgs,
            "max_tokens": 4096,
        }
        
        if system_msg:
            kwargs["system"] = system_msg
        
        if tools:
            kwargs["tools"] = self._format_tools_for_anthropic(tools)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: self.client.messages.create(**kwargs))
        
        content = "".join([b.text for b in response.content if hasattr(b, 'text')])
        # Handle tool calls in response if any...
        return MockMessage(content=content, role="assistant")

    async def _chat_anthropic_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, on_token: Optional[Any] = None) -> Any:
        import queue, threading
        system_msg, anthropic_msgs = self._format_messages_for_anthropic(messages)
        
        kwargs = {
            "model": self.deployment_name,
            "messages": anthropic_msgs,
            "max_tokens": 4096,
            "system": system_msg
        }
        if tools: kwargs["tools"] = self._format_tools_for_anthropic(tools)

        q = queue.Queue()
        def worker():
            try:
                with self.client.messages.stream(**kwargs) as stream:
                    for event in stream: q.put(('event', event))
                q.put(('done', None))
            except Exception as e: q.put(('error', e))

        threading.Thread(target=worker, daemon=True).start()
        
        acc_text = ""
        acc_tools = []
        
        while True:
            try:
                msg_type, data = q.get(timeout=60)
                if msg_type == 'done': break
                if msg_type == 'error': raise data
                
                event = data
                if event.type == 'content_block_delta' and hasattr(event.delta, 'text'):
                    acc_text += event.delta.text
                    if on_token:
                        import inspect
                        if inspect.iscoroutinefunction(on_token): await on_token(event.delta.text)
                        else: on_token(event.delta.text)
                elif event.type == 'content_block_start' and event.content_block.type == 'tool_use':
                    acc_tools.append({"id": event.content_block.id, "name": event.content_block.name, "args": ""})
                elif event.type == 'content_block_delta' and hasattr(event.delta, 'partial_json'):
                    if acc_tools: acc_tools[-1]["args"] += event.delta.partial_json
            except queue.Empty: break

        final_tool_calls = [ToolCall(t["id"], t["name"], t["args"]) for t in acc_tools]
        return MockMessage(content=acc_text, role="assistant", tool_calls=final_tool_calls or None)

    def _format_messages_for_anthropic(self, messages: List[Dict[str, Any]]):
        from .utils import extract_content
        system_content = None
        anthropic_msgs = []
        
        for msg in messages:
            if msg["role"] == "system":
                # Ensure system content is formatted as blocks if it's a string
                content = msg["content"]
                if isinstance(content, str):
                    system_content = [{"type": "text", "text": content}]
                elif isinstance(content, list):
                    system_content = content
                else:
                    # If it's not a string or list, treat it as a single text block
                    system_content = [{"type": "text", "text": str(content)}]
            elif msg["role"] == "tool":
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}]
                })
            else:
                raw = msg.get("content", "")
                text, images = extract_content(raw)
                blocks = []
                for img in images:
                    data = img["data"]
                    if isinstance(data, bytes): data = base64.b64encode(data).decode('utf-8')
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": img.get("mime_type", "image/png"), "data": data}})
                if text: blocks.append({"type": "text", "text": text})
                anthropic_msgs.append({"role": msg["role"], "content": blocks or ""})
        
        return system_content, anthropic_msgs

    def _format_tools_for_anthropic(self, tools: List[Dict[str, Any]]):
        a_tools = []
        for t in tools:
            f = t.get("function", {})
            a_tools.append({"name": f.get("name"), "description": f.get("description"), "input_schema": f.get("parameters")})
        return a_tools

    # --- AI Inference Implementation ---

    async def _chat_inference(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """Generic implementation for Azure AI Inference (MaaS)."""
        # If we have a client initialized, use it (same as OpenAI)
        if self.client:
            return await self._chat_openai(messages, tools)
            
        # Fallback to raw HTTP if client init failed (legacy/fallback)
        import httpx
        url = f"{self.endpoint}/chat/completions?api-version={self.api_version}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        
        payload = {"messages": messages, "model": self.deployment_name}
        if tools: payload["tools"] = tools

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            # Return normalized message
            msg = data["choices"][0]["message"]
            return MockMessage(content=msg.get("content", ""), role="assistant", tool_calls=msg.get("tool_calls"))

    # --- Utils ---

    def get_model_name(self) -> str:
        return self.deployment_name

    def _supports_vision(self) -> bool:
        """Helper for CapabilityDetector to check vision support based on model type."""
        name_lower = self.deployment_name.lower()
        if self.model_type == self.MODEL_TYPE_OPENAI:
            return any(kw in name_lower for kw in ["gpt-4o", "vision", "o1"])
        elif self.model_type == self.MODEL_TYPE_ANTHROPIC:
            return any(kw in name_lower for kw in ["claude-3", "sonnet", "haiku", "opus"])
        return False
