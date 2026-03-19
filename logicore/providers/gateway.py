"""
Provider Gateway Layer - Normalizes differences between LLM providers.

This layer provides a unified interface for all LLM providers, handling:
- Message format normalization (input/output)
- Chat and streaming API differences
- Usage tracking
- Tool calling conventions
- Model-specific quirks

Benefits:
- Agent.py doesn't need provider-specific logic
- Easy to add new providers
- Consistent behavior across all providers
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Union, Tuple
import json
import inspect


class NormalizedMessage:
    """Standard message format used across all gateways."""
    
    def __init__(self, role: str, content: str = "", tool_calls: List[Dict[str, Any]] = None, 
                 name: str = None, tool_call_id: str = None):
        self.role = role  # "user", "assistant", "system", "tool"
        self.content = content
        self.tool_calls = tool_calls or []
        self.name = name  # For tool messages
        self.tool_call_id = tool_call_id  # For tool messages
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to standard dict format."""
        result = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.name:
            result["name"] = self.name
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        return result


class ProviderGateway(ABC):
    """
    Abstract base gateway for LLM providers.
    
    Defines the standard interface that all provider gateways must implement.
    """
    
    def __init__(self, provider):
        """
        Initialize gateway with a provider instance.
        
        Args:
            provider: An LLMProvider instance (Gemini, Groq, etc.)
        """
        self.provider = provider
        self.model_name = provider.get_model_name() if hasattr(provider, 'get_model_name') else provider.model_name
        self.provider_name = getattr(provider, 'provider_name', 'unknown')
    
    @abstractmethod
    async def normalize_for_provider(self, messages: List[Dict[str, Any]]) -> Tuple[Any, Any]:
        """
        Convert standard message format to provider-specific format.
        
        Args:
            messages: Standard OpenAI-like message list
            
        Returns:
            Tuple of (normalized_messages, provider_specific_kwargs)
        """
        pass
    
    @abstractmethod
    async def normalize_response(self, response: Any) -> NormalizedMessage:
        """
        Convert provider-specific response to standard format.
        
        Args:
            response: Provider's native response object
            
        Returns:
            NormalizedMessage with role, content, and tool_calls
        """
        pass
    
    @abstractmethod
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> NormalizedMessage:
        """
        Execute a chat request through the provider.
        
        Args:
            messages: Standard message list
            tools: Standard tool definitions
            
        Returns:
            Normalized message response
        """
        pass
    
    @abstractmethod
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> NormalizedMessage:
        """
        Execute a streaming chat request.
        
        Args:
            messages: Standard message list
            tools: Standard tool definitions
            on_token: Optional callback for each token
            
        Returns:
            Normalized message response after streaming completes
        """
        pass
    
    async def get_usage(self) -> Dict[str, Any]:
        """
        Get token usage information if available.
        
        Returns:
            Dict with keys: input_tokens, output_tokens, total_tokens
        """
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        }


class OpenAIGateway(ProviderGateway):
    """Gateway for OpenAI-compatible providers (OpenAI, Groq, etc.)."""
    
    async def normalize_for_provider(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict], Dict]:
        """OpenAI format is already standard, minimal transformation needed."""
        # OpenAI expects all message types including tool/function results
        # Don't filter anything out - the provider SDK will handle it
        return messages, {}
    
    async def normalize_response(self, response: Any) -> NormalizedMessage:
        """Convert OpenAI response to normalized format."""
        content = response.content or ""
        tool_calls = []
        
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tc in response.tool_calls:
                if hasattr(tc, 'function'):
                    tool_calls.append({
                        "id": getattr(tc, 'id', ''),
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    })
        
        return NormalizedMessage(
            role=getattr(response, 'role', 'assistant'),
            content=content,
            tool_calls=tool_calls
        )
    
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> NormalizedMessage:
        """Execute chat through OpenAI-compatible provider."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat(norm_messages, tools=tools)
        return await self.normalize_response(response)
    
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> NormalizedMessage:
        """Execute streaming chat through OpenAI-compatible provider."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat_stream(norm_messages, tools=tools, on_token=on_token)
        return await self.normalize_response(response)


class GeminiGateway(ProviderGateway):
    """Gateway for Google Gemini API."""
    
    async def normalize_for_provider(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict], Dict]:
        """
        Gemini requires special handling:
        - System messages become system_instruction
        - Messages formatted as Contents with Parts
        """
        system_instruction = None
        normalized = []
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "system":
                # Extract system instruction
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_instruction = content
                elif isinstance(content, list):
                    texts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    system_instruction = " ".join(texts)
                continue
            
            # Keep non-system messages
            normalized.append(msg)
        
        return normalized, {"system_instruction": system_instruction}
    
    async def normalize_response(self, response: Any) -> NormalizedMessage:
        """Convert Gemini response to normalized format."""
        content = ""
        tool_calls = []
        
        # GeminiProvider.chat() returns a pre-parsed dict, not the raw SDK response
        if isinstance(response, dict):
            content = response.get('content', "")
            tool_calls = response.get('tool_calls', [])
            role = response.get('role', 'assistant')
            return NormalizedMessage(
                role=role,
                content=content,
                tool_calls=tool_calls or []
            )
        
        # Fallback: handle raw SDK response objects (if provider ever returns them)
        if hasattr(response, 'text'):
            content = response.text or ""
        
        # Handle Gemini tool calls if present
        if hasattr(response, 'candidates'):
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call'):
                            func_call = part.function_call
                            tool_calls.append({
                                "id": getattr(func_call, 'name', ''),
                                "type": "function",
                                "function": {
                                    "name": func_call.name,
                                    "arguments": json.dumps(func_call.args) if func_call.args else "{}"
                                }
                            })
        
        return NormalizedMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls
        )
    
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> NormalizedMessage:
        """Execute chat through Gemini."""
        norm_messages, kwargs = await self.normalize_for_provider(messages)
        
        # Merge system instruction into provider call
        response = await self.provider.chat(norm_messages, tools=tools)
        return await self.normalize_response(response)
    
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> NormalizedMessage:
        """Execute streaming chat through Gemini."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat_stream(norm_messages, tools=tools, on_token=on_token)
        return await self.normalize_response(response)


class AzureGateway(ProviderGateway):
    """Gateway for Azure AI (handles multiple backend types: OpenAI, Anthropic, Inference)."""
    
    async def normalize_for_provider(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict], Dict]:
        """Azure typically uses OpenAI-like format with all message types."""
        # Include all message types including tool results
        return messages, {}
    
    async def normalize_response(self, response: Any) -> NormalizedMessage:
        """Convert Azure response to normalized format."""
        content = getattr(response, 'content', "")
        tool_calls = []
        
        # Handle Azure's tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tc in response.tool_calls:
                if hasattr(tc, 'function'):
                    tool_calls.append({
                        "id": getattr(tc, 'id', ''),
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    })
        
        return NormalizedMessage(
            role=getattr(response, 'role', 'assistant'),
            content=content,
            tool_calls=tool_calls
        )
    
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> NormalizedMessage:
        """Execute chat through Azure."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat(norm_messages, tools=tools)
        return await self.normalize_response(response)
    
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> NormalizedMessage:
        """Execute streaming chat through Azure."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat_stream(norm_messages, tools=tools, on_token=on_token)
        return await self.normalize_response(response)


class OllamaGateway(ProviderGateway):
    """Gateway for Ollama (local inference)."""
    
    async def normalize_for_provider(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict], Dict]:
        """Ollama uses standard OpenAI-like format with all message types."""
        # Include all message types including tool results
        return messages, {}
    
    async def normalize_response(self, response: Any) -> NormalizedMessage:
        """Convert Ollama response to normalized format."""
        # Ollama returns a dict: {'role': ..., 'content': ..., 'tool_calls': ...}
        if isinstance(response, dict):
            content = response.get('content', "")
            tool_calls = response.get('tool_calls', [])
            role = response.get('role', 'assistant')
        else:
            # Fallback for object-based responses
            content = getattr(response, 'content', "")
            tool_calls = getattr(response, 'tool_calls', [])
            role = getattr(response, 'role', 'assistant')
        
        return NormalizedMessage(
            role=role,
            content=content,
            tool_calls=tool_calls or []
        )
    
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> NormalizedMessage:
        """Execute chat through Ollama."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat(norm_messages, tools=tools)
        return await self.normalize_response(response)
    
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> NormalizedMessage:
        """Execute streaming chat through Ollama."""
        norm_messages, _ = await self.normalize_for_provider(messages)
        response = await self.provider.chat_stream(norm_messages, tools=tools, on_token=on_token)
        return await self.normalize_response(response)


def get_gateway_for_provider(provider) -> ProviderGateway:
    """
    Factory function to get the appropriate gateway for a provider.
    
    Args:
        provider: An LLMProvider instance
        
    Returns:
        Appropriate ProviderGateway subclass instance
    """
    provider_name = getattr(provider, 'provider_name', 'unknown').lower()
    
    GATEWAY_MAP = {
        'openai': OpenAIGateway,
        'groq': OpenAIGateway,  # Groq is OpenAI-compatible
        'gemini': GeminiGateway,
        'azure': AzureGateway,
        'ollama': OllamaGateway,
    }
    
    gateway_class = GATEWAY_MAP.get(provider_name, OpenAIGateway)
    return gateway_class(provider)
