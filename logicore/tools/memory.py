"""
MemoryTool - Removed.

This module has been stripped of all functionality.
Kept for import compatibility only.
"""

from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class MemoryToolParams(BaseModel):
    """Placeholder schema for backward compatibility."""
    action: Literal["store", "search", "list", "export", "set_project"] = Field(
        "list",
        description="Memory functionality has been removed"
    )
    memory_type: Optional[str] = Field(None)
    title: Optional[str] = Field(None)
    content: Optional[str] = Field(None)
    query: Optional[str] = Field(None)
    project_id: Optional[str] = Field(None)
    tags: Optional[List[str]] = Field(None)
    limit: Optional[int] = Field(10)


class MemoryTool(BaseTool):
    """
    Stub tool - memory functionality has been removed.
    """

    name = "memory"
    description = "Memory functionality has been removed. This tool is a no-op."
    args_schema = MemoryToolParams

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(
            success=False,
            error="Memory functionality has been removed from this version."
        )
