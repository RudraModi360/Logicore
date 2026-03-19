import json
from typing import List, Dict, Any
from logicore.providers.base import LLMProvider

class ContextMiddleware:
    def __init__(self, llm_provider: LLMProvider, token_threshold: int = 100000):
        self.llm = llm_provider
        # Increase threshold significantly for vision models, as base64 is heavy
        self.threshold = token_threshold if token_threshold > 100000 else 200000
        # Keep last N messages raw to preserve immediate context flow
        self.preserve_recent_count = 10 

    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Estimate token count using character length heuristic (1 token ~= 4 chars).
        Ignores base64 image data to avoid artificial spikes.
        """
        from logicore.providers.utils import extract_content
        total_chars = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, (str, list)):
                text, _, _ = extract_content(content)
                total_chars += len(text)
            
            # Also count tool calls/results if present (briefly)
            if 'tool_calls' in msg:
                total_chars += len(str(msg['tool_calls'])) // 10
        
        return total_chars // 4

    async def _summarize_chunk(self, messages: List[Dict[str, Any]]) -> str:
        """
        Summarize a list of messages into a single concise paragraph.
        """
        from logicore.providers.utils import extract_content
        # Convert messages to a text block
        conversation_text = ""
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            
            text, images, _ = extract_content(content)
            image_note = f" [Contains {len(images)} image(s)]" if images else ""
            
            conversation_text += f"{role.upper()}: {text}{image_note}\n"

        prompt = f"""
You are a context manager. The following is a segment of a conversation history that is being compressed.
Summarize the key information, decisions, and context from this segment so that the AI can continue the conversation without losing the thread.
Focus on facts and the state of the conversation.

CONVERSATION SEGMENT:
{conversation_text}

SUMMARY:
"""
        # We use a temporary message list for the summarization call
        summ_messages = [{"role": "user", "content": prompt}]
        
        try:
            response = await self.llm.chat(summ_messages, tools=None)
            
            content = ""
            if isinstance(response, dict):
                content = response.get('content', '')
            else:
                content = getattr(response, 'content', '')
                
            return content.strip()
        except Exception as e:
            print(f"[ContextMiddleware] Summarization failed: {e}")
            return "Error generating summary."

    async def manage_context(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Checks token count and summarizes if threshold is exceeded.
        Returns the (potentially modified) list of messages.
        """
        current_tokens = self._estimate_tokens(messages)
        
        if current_tokens < self.threshold:
            return messages
            
        print(f"[ContextMiddleware] Token count ({current_tokens}) exceeds threshold ({self.threshold}). Summarizing...")

        # Strategy:
        # 1. Keep System Message (usually index 0)
        # 2. Keep last N messages (preserve_recent_count)
        # 3. Summarize everything in between
        
        if len(messages) <= self.preserve_recent_count + 2:
            # Not enough messages to summarize effectively
            return messages

        system_msg = None
        start_index = 0
        
        # Identify system message
        if messages[0].get('role') == 'system':
            system_msg = messages[0]
            start_index = 1
            
        # Define the chunk to summarize
        # From start_index to (end - preserve_recent)
        end_index = len(messages) - self.preserve_recent_count
        
        if start_index >= end_index:
            return messages
            
        chunk_to_summarize = messages[start_index:end_index]
        recent_messages = messages[end_index:]
        
        # Generate Summary
        summary_text = await self._summarize_chunk(chunk_to_summarize)
        
        # Create new history
        new_history = []
        if system_msg:
            new_history.append(system_msg)
            
        # Add summary message
        # We can add it as a 'system' message or a special 'user' message context
        summary_msg = {
            "role": "system", 
            "content": f"=== Previous Conversation Summary ===\n{summary_text}\n====================================="
        }
        new_history.append(summary_msg)
        
        # Append recent messages
        new_history.extend(recent_messages)
        
        new_token_count = self._estimate_tokens(new_history)
        print(f"[ContextMiddleware] Context compressed. New token count: {new_token_count}")
        
        return new_history
