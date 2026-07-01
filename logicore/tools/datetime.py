from typing import Optional, Literal, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
import json
from .notes import NotesTool
from .think import ThinkTool
from .bash import SmartBashTool


class DateTimeParams(BaseModel):
    operation: Literal["now", "format", "parse", "diff"] = Field(
        "now",
        description="Operation to perform: 'now' (get current time), 'format' (format a date), 'parse' (parse date string), 'diff' (time difference)"
    )
    format_string: Optional[str] = Field(
        None,
        description="Format string for 'format' operation (e.g., '%Y-%m-%d %H:%M:%S')"
    )
    timezone: Optional[str] = Field(
        None,
        description="Timezone name (e.g., 'UTC', 'America/New_York'). Defaults to local."
    )


class DateTimeTool(BaseTool):
    """Get current date, time, and perform date/time operations."""
    
    name = "datetime"
    description = (
        "Get current date/time or perform date operations. "
        "Use for: checking current time, formatting dates, scheduling information. "
        "Operations: 'now' (default) returns current date/time in multiple formats."
    )
    args_schema = DateTimeParams
    
    def run(self, operation: str = "now", format_string: str = None, 
            timezone: str = None, action: str = None, **kwargs) -> ToolResult:
        if action and not operation:
            operation = action
        elif action:
            operation = action
            
        try:
            now = datetime.now()
            
            if operation in ["now", "get", "current"]:
                result = {
                    "iso": now.isoformat(),
                    "date": now.strftime("%Y-%m-%d"),
                    "time": now.strftime("%H:%M:%S"),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "day_of_week": now.strftime("%A"),
                    "timestamp": now.timestamp(),
                    "human_readable": now.strftime("%B %d, %Y at %I:%M %p")
                }
                return ToolResult(success=True, content=json.dumps(result, indent=2))
            
            elif operation == "format":
                if not format_string:
                    format_string = "%Y-%m-%d %H:%M:%S"
                formatted = now.strftime(format_string)
                return ToolResult(success=True, content=formatted)
            
            else:
                result = {
                    "iso": now.isoformat(),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "human_readable": now.strftime("%B %d, %Y at %I:%M %p")
                }
                return ToolResult(success=True, content=json.dumps(result, indent=2))
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def get_smart_agent_tools() -> List[BaseTool]:
    """Get all tools for the Smart Agent."""
    return [
        DateTimeTool(),
        NotesTool(),
        SmartBashTool(),
        ThinkTool()
    ]


def get_smart_agent_tool_schemas() -> List[Dict[str, Any]]:
    """Get schemas for all Smart Agent tools."""
    return [tool.schema for tool in get_smart_agent_tools()]
