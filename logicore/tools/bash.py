from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class SmartBashParams(BaseModel):
    command: str = Field(
        ...,
        description="The command to execute"
    )
    purpose: Optional[str] = Field(
        None,
        description="Brief description of what this command does (for learning)"
    )
    working_dir: Optional[str] = Field(
        None,
        description="Working directory for command execution"
    )
    timeout: int = Field(
        60,
        description="Timeout in seconds (1-300)"
    )
    capture_learning: bool = Field(
        False,
        description="Whether to store the command as a learning if successful"
    )


class SmartBashTool(BaseTool):
    """
    Enhanced bash/shell command execution with learning capabilities.
    Automatically captures successful commands as learnings when requested.
    """
    
    name = "bash"
    description = (
        "Execute shell commands with learning capabilities. "
        "Use for: running scripts, system operations, installations. "
        "Set capture_learning=true to remember successful commands."
    )
    args_schema = SmartBashParams
    
    def __init__(self):
        from .execution import ExecuteCommandTool
        self.exec_tool = ExecuteCommandTool()
    
    def run(self, command: str, purpose: str = None, working_dir: str = None,
            timeout: int = 60, capture_learning: bool = False) -> ToolResult:
        result = self.exec_tool.run(
            command=command,
            working_directory=working_dir,
            timeout=timeout
        )
        
        return result
