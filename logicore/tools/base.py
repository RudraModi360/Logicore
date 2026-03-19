from abc import ABC, abstractmethod
from typing import Any, Dict, Type, Optional
from pydantic import BaseModel

class ToolResult(dict):
    def __init__(self, success: bool, content: Any = None, error: str = None):
        super().__init__()
        self['success'] = success
        if content is not None:
            self['content'] = content
        if error is not None:
            self['error'] = error
    
    def to_dict(self):
        return {'success': self['success'], 'content': self.get('content', None), 'error': self.get('error', None)}

class BaseTool(ABC):
    name: str
    description: str
    args_schema: Type[BaseModel]

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """Execute the tool logic."""
        pass

    @property
    def schema(self) -> Dict[str, Any]:
        """Return the JSON schema for the tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema()
            }
        }
