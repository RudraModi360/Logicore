import ollama
from typing import List, Dict, Any, Optional, Callable
from .base import LLMProvider

class OllamaProvider(LLMProvider):
    provider_name = "ollama"
    
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        self.model_name = model_name
        self.client = ollama.Client(**kwargs)

    def _prepare_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """Prepare and filter messages for Ollama. Returns (filtered_messages, has_images)."""
        from .utils import extract_content
        
        filtered_messages = []
        has_images = False
        
        for msg in messages:
            role = msg.get("role")
            raw_content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            
            text_content, images = extract_content(raw_content)
            
            ollama_msg = {"role": role, "content": text_content}
            
            if images:
                has_images = True
                ollama_images = []
                for img in images:
                    if img.get("data"):
                        import base64
                        ollama_images.append(base64.b64encode(img["data"]).decode('utf-8'))
                
                if ollama_images:
                    ollama_msg["images"] = ollama_images
            
            if tool_calls:
                if isinstance(tool_calls, dict):
                    if 'required' in tool_calls or 'properties' in tool_calls:
                        tool_calls = None
                    else:
                        tool_calls = [tool_calls]
                
                if tool_calls:
                    clean_calls = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            if 'required' in tc or 'properties' in tc:
                                continue
                            clean_calls.append(tc)
                        elif hasattr(tc, 'function'):
                            # Handle Ollama's native ToolCall objects
                            # Retain name/arguments so it passes correctly back into history
                            clean_call = {
                                'function': {
                                    'name': getattr(tc.function, 'name', ''),
                                    'arguments': getattr(tc.function, 'arguments', {})
                                }
                            }
                            clean_calls.append(clean_call)
                    
                    if clean_calls:
                        ollama_msg["tool_calls"] = clean_calls
            
            # Preserve tool result metadata for proper LLM context
            if "name" in msg:
                ollama_msg["name"] = msg["name"]
            if "tool_call_id" in msg:
                ollama_msg["tool_call_id"] = msg["tool_call_id"]
            
            if role and (text_content or images or tool_calls):
                filtered_messages.append(ollama_msg)
        
        return filtered_messages, has_images

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """Standard non-streaming chat."""
        filtered_messages, has_images = self._prepare_messages(messages)
        
        if not filtered_messages:
            raise ValueError("No valid messages to send to Ollama")
            
        if has_images and not self._supports_vision():
            raise ValueError(f"Ollama model '{self.model_name}' does not support vision capabilities.")

        # Simplify tools for Ollama compatibility
        from .utils import simplify_tool_schema
        # Disable tools if images are present (vision models usually don't support tools)
        if has_images:
            simplified_tools = None
        else:
            simplified_tools = [simplify_tool_schema(t) for t in tools] if tools else None

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=filtered_messages,
                tools=simplified_tools,
            )
            
            if not response or 'message' not in response:
                raise ValueError("Ollama returned invalid response structure")
            
            message = response['message']
            
            if not message.get('content') and not message.get('tool_calls'):
                raise ValueError("Ollama returned empty message with no content or tool calls")
            
            return message
            
        except Exception as e:
            error_msg = str(e)
            if "empty" in error_msg.lower() or "invalid" in error_msg.lower():
                raise ValueError(f"Ollama error: {error_msg}. Try using a different model or check if Ollama is running properly.")
            if "support" in error_msg.lower() and "image" in error_msg.lower():
                raise ValueError("Model not support to given data type") from e
            raise

    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> Any:
        """
        Streaming chat that yields tokens progressively.
        Returns the final complete message dict.
        Supports both sync and async on_token callbacks.
        """
        import asyncio
        import inspect
        
        filtered_messages, has_images = self._prepare_messages(messages)
        
        if not filtered_messages:
            raise ValueError("No valid messages to send to Ollama")
            
        if has_images and not self._supports_vision():
            raise ValueError(f"Ollama model '{self.model_name}' does not support vision capabilities.")

        # Use a queue to pass tokens from sync thread to async handler
        token_queue = asyncio.Queue()
        done_event = asyncio.Event()
        result_holder = {"message": None, "error": None}
        
        def sync_stream():
            """Run the blocking stream iteration in a thread."""
            full_content = ""
            tool_calls = None
            
            # Simplify tools for Ollama compatibility
            from .utils import simplify_tool_schema
            # Disable tools if images are present (vision models usually don't support tools)
            if has_images:
                simplified_tools = None
            else:
                simplified_tools = [simplify_tool_schema(t) for t in tools] if tools else None

            try:
                stream = self.client.chat(
                    model=self.model_name,
                    messages=filtered_messages,
                    tools=simplified_tools,
                    stream=True
                )
                
                for chunk in stream:
                    # Handle both dictionary and object formats from Ollama client
                    msg = None
                    if isinstance(chunk, dict) and 'message' in chunk:
                        msg = chunk['message']
                    elif hasattr(chunk, 'message'):
                        msg = chunk.message
                        
                    if msg is not None:
                        # Extract content
                        token = None
                        if isinstance(msg, dict):
                            token = msg.get('content')
                        else:
                            token = getattr(msg, 'content', None)
                            
                        if token:
                            full_content += token
                            asyncio.run_coroutine_threadsafe(
                                token_queue.put(token),
                                loop
                            )
                            
                        # Extract thinking
                        think_token = None
                        if isinstance(msg, dict):
                            think_token = msg.get('thinking')
                        else:
                            think_token = getattr(msg, 'thinking', None)
                            
                        if think_token:
                            full_content += think_token
                            asyncio.run_coroutine_threadsafe(
                                token_queue.put(think_token),
                                loop
                            )
                        
                        # Extract tool calls
                        if isinstance(msg, dict):
                            tc = msg.get('tool_calls')
                        else:
                            tc = getattr(msg, 'tool_calls', None)
                            
                        if tc:
                            tool_calls = tc
                
                final_message = {"role": "assistant", "content": full_content}
                if tool_calls:
                    final_message["tool_calls"] = tool_calls
                    
                result_holder["message"] = final_message
                
            except Exception as e:
                result_holder["error"] = e
            finally:
                # Signal completion
                asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

        loop = asyncio.get_event_loop()
        
        # Start the blocking stream in a thread
        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(sync_stream)
        
        # Process tokens as they arrive
        while True:
            token = await token_queue.get()
            if token is None:
                break
            if on_token:
                # Handle both sync and async callbacks
                if inspect.iscoroutinefunction(on_token):
                    await on_token(token)
                else:
                    on_token(token)
        
        # Wait for thread to complete
        await asyncio.get_event_loop().run_in_executor(None, future.result)
        executor.shutdown(wait=False)
        
        if result_holder["error"]:
            raise result_holder["error"]
            
        return result_holder["message"]


    def _supports_vision(self) -> bool:
        try:
            info = self.client.show(self.model_name)
            details = info.get('details', {})
            families = details.get('families', []) or []
            if details.get('family'):
                families.append(details.get('family'))
            
            # Check families
            vision_families = ['clip', 'vision', 'momo', 'llava', 'multimodal', 'mllama']
            for f in families:
                f_lower = f.lower()
                if any(v in f_lower for v in vision_families):
                    return True
            
            # Check model name for vision keywords
            name = self.model_name.lower()
            vision_keywords = ['llava', 'vision', 'minicpm', 'vl', 'pixtral', 'moondream', 'bakllava']
            if any(k in name for k in vision_keywords):
                return True
                
            return False
        except Exception:
            return False

    def get_model_name(self) -> str:
        return self.model_name

    def pull_model(self) -> bool:
        """Pulls the model if it's not already present locally."""
        try:
            # Check if model exists
            local_models = self.client.list()
            model_exists = any(m['name'] == self.model_name or m['name'].startswith(f"{self.model_name}:") for m in local_models.get('models', []))
            
            if not model_exists:
                print(f"[Ollama] Pulling model: {self.model_name}...")
                self.client.pull(self.model_name)
                return True
            return False
        except Exception as e:
            print(f"[Ollama] Error pulling model {self.model_name}: {e}")
            return False
