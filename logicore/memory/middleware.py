import json
from typing import Dict, Any, List
from logicore.providers.base import LLMProvider
from logicore.memory.storage import PersistentMemoryStore

class MemoryMiddleware:
    def __init__(self, llm_provider: LLMProvider, storage: PersistentMemoryStore):
        self.llm = llm_provider
        self.storage = storage

    async def _extract_memory(self, text: str, context: str = "user input") -> Dict[str, Any]:
        """
        Uses LLM to decide if text contains useful long-term information.
        """
        prompt = f"""
You are a memory extractor for an AI assistant. 
Analyze the following {context} and decide if it contains important long-term information that should be remembered for future sessions.

Criteria for memory:
- User preferences, goals, or personal details.
- Important facts established during the conversation.
- Specific work context or project details.
- DO NOT store: Greetings, small talk, emotional reactions, or temporary clarifications.

TEXT:
"{text}"

Respond strictly in JSON format:
{{
  "should_remember": true/false,
  "memory_content": "Concise summary of the fact to store (if true)",
  "memory_type": "preference" | "fact" | "goal" | "context"
}}
"""
        # We need a way to call the LLM that returns just the JSON. 
        # The provider.chat expects a list of messages.
        messages = [{"role": "user", "content": prompt}]
        
        try:
            response = await self.llm.chat(messages, tools=None)
            
            content = ""
            if isinstance(response, dict):
                content = response.get('content', '')
            else:
                content = getattr(response, 'content', '')

            # Basic cleanup for JSON parsing
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            
            return json.loads(content.strip())
        except Exception as e:
            # Fail silently on memory extraction errors to not disrupt main flow
            # Fail silently on memory extraction errors to not disrupt main flow
            # print(f"[MemoryMiddleware] Extraction failed: {e}")
            return {"should_remember": False}

    async def process_user_input(self, session_id: str, user_input: str) -> List[Dict[str, Any]]:
        """
        1. Extract insights from user input and store if valuable.
        2. Retrieve relevant memories to augment the agent's context.
        """
        # 1. Extraction (Fire and forget or await? Await for now to ensure consistency)
        extraction = await self._extract_memory(user_input, context="user input")
        
        if extraction.get("should_remember"):
            self.storage.add_memory(
                session_id=session_id, # Or 'global' if we want it everywhere? Let's keep it session-linked but retrievable.
                memory_type=extraction.get("memory_type", "general"),
                content=extraction.get("memory_content", "")
            )
            print(f"[Memory] Stored user insight: {extraction.get('memory_content')}")

        # 2. Retrieval
        # For now, get recent memories for this session + some global ones if we implemented that.
        # We'll just fetch the last 10 memories for this session.
        memories = self.storage.get_memories(session_id, limit=10)
        return memories

    async def process_agent_output(self, session_id: str, agent_output: str):
        """
        Extract insights from agent output (e.g., if the agent generated a new plan or fact).
        """
        extraction = await self._extract_memory(agent_output, context="agent output")
        
        if extraction.get("should_remember"):
            self.storage.add_memory(
                session_id=session_id,
                memory_type=extraction.get("memory_type", "general"),
                content=extraction.get("memory_content", "")
            )
            print(f"[Memory] Stored agent insight: {extraction.get('memory_content')}")
