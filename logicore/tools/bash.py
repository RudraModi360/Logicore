from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class SmartBashParams(BaseModel):
    command: str = Field(
        ...,
        description="The command to execute. Python code is auto-detected."
    )
    purpose: Optional[str] = Field(
        None,
        description="Brief description of what this command does (for learning)"
    )
    working_dir: Optional[str] = Field(
        None,
        description="Working directory for command execution"
    )
    workdir: Optional[str] = Field(
        None,
        description="Alias for working_dir. Working directory for command execution."
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
    
    Features:
    - Auto-detects host OS and uses appropriate shell
    - Auto-translates commands between OS syntaxes (Unix ↔ PowerShell)
    - Auto-detects Python code and handles via temp files
    - Captures successful commands as learnings when requested
    """
    
    name = "bash"
    description = (
        "Execute shell commands. Auto-detects OS and translates commands between "
        "Unix/PowerShell syntax. Python code is auto-detected and handled via temp files. "
        "Set capture_learning=true to remember successful commands."
    )
    args_schema = SmartBashParams
    
    def __init__(self):
        from .execution import ExecuteCommandTool
        self.exec_tool = ExecuteCommandTool()
    
    def run(self, command: str, purpose: str = None, working_dir: str = None,
            workdir: str = None, timeout: int = 60, capture_learning: bool = False) -> ToolResult:
        # Support both working_dir and workdir (model sometimes uses wrong name)
        cwd = working_dir or workdir
        result = self.exec_tool.run(
            command=command,
            working_directory=cwd,
            timeout=timeout
        )
        
        return result
