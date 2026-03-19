from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable

class LLMProvider(ABC):
    @abstractmethod
    def __init__(self, model_name: str, api_key: Optional[str] = None, **kwargs):
        pass

    @abstractmethod
    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        """
        Sends a chat request to the LLM.
        
        Args:
            messages: A list of message dictionaries (role, content).
            tools: A list of tool definitions (optional).
            
        Returns:
            A response object containing the message content and potential tool calls.
            The structure should be normalized to a common format if possible, 
            or the Agent should handle different return types (but normalization is better).
        """
        pass
    
    @abstractmethod
    def get_model_name(self) -> str:
        pass

    @abstractmethod
    async def chat_stream(
        self, 
        messages: List[Dict[str, Any]], 
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None
    ) -> Any:
        """
        Streaming version of chat.
        
        Args:
            messages: A list of message dictionaries.
            tools: A list of tool definitions.
            on_token: A callback function for each token.
            
        Returns:
            A final response object after streaming is complete.
        """
        pass
