from abc import ABC, abstractmethod
from typing import Any, Dict, Type, Optional
from pydantic import BaseModel

class ToolResult(dict):
    def __init__(self, success: bool, content: Any = None, error: str = None, **extra):
        super().__init__()
        self['success'] = success
        if content is not None:
            self['content'] = content
        if error is not None:
            self['error'] = error
        # Extra structured fields (e.g. error_category, retryable,
        # should_rotate_credential) are preserved verbatim so tool-execution
        # consumers can make autonomous recovery decisions.
        for key, value in extra.items():
            self[key] = value

    def to_dict(self):
        return dict(self)

class BaseTool(ABC):
    """
    Base class for all tools in Logicore.
    
    Based on Claude Code's Tool.ts pattern:
    - is_read_only(): Whether tool only reads data (no writes)
    - is_destructive(): Whether tool performs irreversible operations
    - interrupt_behavior(): What happens on interrupt ('cancel' or 'block')
    """
    name: str
    description: str
    args_schema: Type[BaseModel]

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """Execute the tool logic."""
        pass

    def is_read_only(self, args: Optional[Dict[str, Any]] = None) -> bool:
        """
        Whether this tool only reads data (no writes).
        
        Based on Claude Code's Tool.ts pattern:
        - Returns True for tools that only read data (no side effects)
        - Returns False for tools that write/modify data (fail-closed)
        
        Default: False (assume writes - fail-closed)
        """
        return False

    def is_destructive(self, args: Optional[Dict[str, Any]] = None) -> bool:
        """
        Whether this tool performs irreversible operations.
        
        Based on Claude Code's Tool.ts pattern:
        - Returns True for tools that perform irreversible operations (delete, overwrite)
        - Returns False for reversible operations
        
        Default: False (not destructive)
        """
        return False

    def interrupt_behavior(self) -> str:
        """
        What happens on interrupt.
        
        Based on Claude Code's Tool.ts pattern:
        - 'cancel': Tool execution is cancelled
        - 'block': Tool execution blocks until complete
        
        Default: 'block' (matches reference default for safer behavior)
        """
        return 'block'

    def is_concurrency_safe(self, args: Optional[Dict[str, Any]] = None) -> bool:
        """
        Whether this tool can run concurrently with other tools.
        
        Based on Claude Code's Tool.ts pattern:
        - Returns True for read-only tools that can run in parallel
        - Returns False for tools that modify state (fail-closed)
        
        Default: False (assume not safe - fail-closed)
        """
        return False

    def is_enabled(self) -> bool:
        """
        Whether this tool is enabled.
        
        Based on Claude Code's Tool.ts pattern:
        - Returns True if tool is available
        - Returns False if tool is disabled (e.g., feature-gated)
        
        Default: True
        """
        return True

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
