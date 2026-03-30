import os
import base64
from groq import Groq
from typing import List, Dict, Any, Optional, Callable, Union
from .base import LLMProvider

class GroqProvider(LLMProvider):
    provider_name = "groq"
    
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("Groq API key is required.")
        self.client = Groq(api_key=self.api_key)

    def _convert_local_images_to_base64(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert local image file paths to base64 data URLs for Groq API."""
        import mimetypes
        
        converted_messages = []
        for msg in messages:
            new_msg = msg.copy()
            content = msg.get("content")
            
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        new_part = part.copy()
                        image_url_data = part.get("image_url")
                        
                        if isinstance(image_url_data, dict):
                            url = image_url_data.get("url")
                        else:
                            url = image_url_data
                        
                        if url and not url.startswith(("http://", "https://", "data:")):
                            url = str(url).replace("\\\\", "\\")
                            if os.path.isfile(url):
                                try:
                                    with open(url, "rb") as f:
                                        image_data = f.read()
                                    mime_type, _ = mimetypes.guess_type(url)
                                    mime_type = mime_type or "image/jpeg"
                                    new_part["image_url"] = {"url": f"data:{mime_type};base64,{base64.b64encode(image_data).decode('utf-8')}"}
                                except Exception as e:
                                    pass
                        
                        new_content.append(new_part)
                    else:
                        new_content.append(part)
                new_msg["content"] = new_content
            
            converted_messages.append(new_msg)
        
        return converted_messages

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        from .utils import extract_content
        
        # Convert local images to base64 data URLs
        messages = self._convert_local_images_to_base64(messages)
        
        # Check for images and model support
        has_images = False
        for msg in messages:
            content = msg.get("content")
            _, images = extract_content(content)
            if images:
                break
        
        if has_images:
            model_lower = self.model_name.lower()
            # Groq's vision model: meta-llama/llama-4-scout
            if "llama-4-scout" not in model_lower and "vision" not in model_lower and "llava" not in model_lower:
                raise ValueError(f"Groq model '{self.model_name}' does not support vision capabilities.")

        # Prepare arguments
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
                raise ValueError(f"Groq model returned empty response (output text or tool calls cannot both be empty). Original error: {error_msg}")
            if "validation" in error_msg.lower() and "image" in error_msg.lower(): # Catch Groq specific validation errors for images
                 raise ValueError("Model not support to given data type") from e
            raise e

    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> Any:
        import inspect
        import asyncio
        
        # Convert local images to base64 data URLs
        messages = self._convert_local_images_to_base64(messages)
        
        # Prepare arguments (logic same as chat)
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            
        try:
            # Groq sync stream
            stream = self.client.chat.completions.create(**kwargs)
            
            accumulated_content = ""
            tool_call_chunks = {}
            
            for chunk in stream:
                if not chunk or not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                
                if hasattr(delta, 'content') and delta.content:
                    token = delta.content
                    accumulated_content += token
                    if on_token:
                        if inspect.iscoroutinefunction(on_token):
                            await on_token(token)
                        else:
                            on_token(token)
                
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
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

            # Reconstruct tool calls if any
            final_tool_calls = None
            if tool_call_chunks:
                from groq.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall
                from groq.types.chat.chat_completion_message_tool_call import Function
                
                final_tool_calls = [
                    ChatCompletionMessageToolCall(
                        id=chunk["id"],
                        type="function",
                        function=Function(name=chunk["name"], arguments=chunk["args"])
                    )
                    for chunk in [tool_call_chunks[i] for i in sorted(tool_call_chunks.keys())]
                ]

            # Return a message-like object
            from groq.types.chat.chat_completion_message import ChatCompletionMessage
            return ChatCompletionMessage(
                role="assistant",
                content=accumulated_content or None,
                tool_calls=final_tool_calls
            )
            
        except Exception as e:
            raise e

    def get_model_name(self) -> str:
        return self.model_name
