from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from logicore.memory.project_memory import (
    ProjectMemory, MemoryType, MemoryEntry, 
    get_project_memory
)
import json
import os


# ============== DateTime Tool ==============

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
        # Accept 'action' as alias for 'operation' (models often use 'action')
        if action and not operation:
            operation = action
        elif action:
            operation = action  # Prefer explicit action if both provided
            
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
                # Default to 'now' for unknown operations
                result = {
                    "iso": now.isoformat(),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "human_readable": now.strftime("%B %d, %Y at %I:%M %p")
                }
                return ToolResult(success=True, content=json.dumps(result, indent=2))
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Notes Tool ==============

class NotesParams(BaseModel):
    action: Literal["add", "list", "search", "delete", "get"] = Field(
        ...,
        description="Action to perform: 'add' (create note), 'list' (show all), 'search' (find notes), 'delete' (remove note), 'get' (get specific note)"
    )
    title: Optional[str] = Field(
        None,
        description="Title for the note (required for 'add')"
    )
    content: Optional[str] = Field(
        None,
        description="Content of the note (required for 'add')"
    )
    query: Optional[str] = Field(
        None,
        description="Search query (required for 'search')"
    )
    note_id: Optional[int] = Field(
        None,
        description="Note ID (required for 'get' and 'delete')"
    )
    tags: Optional[List[str]] = Field(
        None,
        description="Tags to categorize the note"
    )


class NotesTool(BaseTool):
    """Personal note-taking tool for storing and retrieving information."""
    
    name = "notes"
    description = (
        "Create, search, and manage personal notes. "
        "Use for: remembering information, quick storage, temporary data. "
        "Notes persist across sessions."
    )
    args_schema = NotesParams
    
    def __init__(self):
        self.notes_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "user_data", "notes"
        )
        os.makedirs(self.notes_dir, exist_ok=True)
        self.notes_file = os.path.join(self.notes_dir, "notes.json")
        self._load_notes()
    
    def _load_notes(self):
        if os.path.exists(self.notes_file):
            with open(self.notes_file, 'r', encoding='utf-8') as f:
                self.notes = json.load(f)
        else:
            self.notes = {"next_id": 1, "items": []}
    
    def _save_notes(self):
        with open(self.notes_file, 'w', encoding='utf-8') as f:
            json.dump(self.notes, f, indent=2, default=str)
    
    def run(self, action: str, title: str = None, content: str = None,
            query: str = None, note_id: int = None, tags: List[str] = None) -> ToolResult:
        try:
            if action == "add":
                if not title or not content:
                    return ToolResult(success=False, error="Title and content are required for 'add'")
                
                note = {
                    "id": self.notes["next_id"],
                    "title": title,
                    "content": content,
                    "tags": tags or [],
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }
                self.notes["items"].append(note)
                self.notes["next_id"] += 1
                self._save_notes()
                
                return ToolResult(success=True, content=f"Note #{note['id']} created: {title}")
            
            elif action == "list":
                if not self.notes["items"]:
                    return ToolResult(success=True, content="No notes found.")
                
                lines = ["# Notes", ""]
                for note in self.notes["items"]:
                    tags_str = f" [{', '.join(note['tags'])}]" if note['tags'] else ""
                    lines.append(f"- **#{note['id']}** {note['title']}{tags_str}")
                
                return ToolResult(success=True, content="\n".join(lines))
            
            elif action == "search":
                if not query:
                    return ToolResult(success=False, error="Query is required for 'search'")
                
                query_lower = query.lower()
                matches = [
                    note for note in self.notes["items"]
                    if query_lower in note["title"].lower() 
                    or query_lower in note["content"].lower()
                    or any(query_lower in tag.lower() for tag in note.get("tags", []))
                ]
                
                if not matches:
                    return ToolResult(success=True, content=f"No notes matching '{query}'")
                
                lines = [f"# Search Results for '{query}'", ""]
                for note in matches:
                    lines.append(f"## #{note['id']}: {note['title']}")
                    lines.append(note['content'][:200] + "..." if len(note['content']) > 200 else note['content'])
                    lines.append("")
                
                return ToolResult(success=True, content="\n".join(lines))
            
            elif action == "get":
                if note_id is None:
                    return ToolResult(success=False, error="Note ID is required for 'get'")
                
                note = next((n for n in self.notes["items"] if n["id"] == note_id), None)
                if not note:
                    return ToolResult(success=False, error=f"Note #{note_id} not found")
                
                return ToolResult(success=True, content=json.dumps(note, indent=2))
            
            elif action == "delete":
                if note_id is None:
                    return ToolResult(success=False, error="Note ID is required for 'delete'")
                
                initial_count = len(self.notes["items"])
                self.notes["items"] = [n for n in self.notes["items"] if n["id"] != note_id]
                
                if len(self.notes["items"]) == initial_count:
                    return ToolResult(success=False, error=f"Note #{note_id} not found")
                
                self._save_notes()
                return ToolResult(success=True, content=f"Note #{note_id} deleted")
            
            else:
                return ToolResult(success=False, error=f"Unknown action: {action}")
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Memory Tool ==============

class MemoryToolParams(BaseModel):
    action: Literal["store", "search", "list", "export", "set_project"] = Field(
        ...,
        description=(
            "Action: 'store' (save new memory), 'search' (find memories), "
            "'list' (get recent memories), 'export' (get LLM-ready context), "
            "'set_project' (set current project context)"
        )
    )
    memory_type: Optional[Literal["approach", "learning", "key_step", "pattern", "preference", "decision", "context"]] = Field(
        None,
        description="Type of memory (required for 'store')"
    )
    title: Optional[str] = Field(
        None,
        description="Title for the memory (required for 'store')"
    )
    content: Optional[str] = Field(
        None,
        description="Content of the memory (required for 'store')"
    )
    query: Optional[str] = Field(
        None,
        description="Search query (required for 'search')"
    )
    project_id: Optional[str] = Field(
        None,
        description="Project ID to filter/associate with"
    )
    tags: Optional[List[str]] = Field(
        None,
        description="Tags for categorization"
    )
    limit: Optional[int] = Field(
        10,
        description="Maximum number of results"
    )


class MemoryTool(BaseTool):
    """
    Access the project memory system for storing and retrieving:
    - Approaches and strategies
    - Learnings from interactions
    - Key steps and decisions
    - Patterns and templates
    """
    
    name = "memory"
    description = (
        "Store and retrieve learned knowledge, approaches, and patterns. "
        "Use for: remembering successful strategies, storing learnings, "
        "recalling past decisions, maintaining project context. "
        "Actions: 'store' (save), 'search' (find), 'list' (recent), 'export' (LLM context)."
    )
    args_schema = MemoryToolParams
    
    def __init__(self):
        self.memory = get_project_memory()
    
    def run(self, action: str = "list", memory_type: str = None, title: str = None,
            content: str = None, query: str = None, project_id: str = None,
            tags: List[str] = None, limit: int = 10, type: str = None, **kwargs) -> ToolResult:
        # Accept 'type' as alias for 'memory_type'
        if type and not memory_type:
            memory_type = type
        
        # Default action handling
        if not action:
            action = "list"
        
        try:
            if action == "store":
                if not memory_type or not title or not content:
                    return ToolResult(
                        success=False, 
                        error="memory_type, title, and content are required for 'store'"
                    )
                
                mem_type = MemoryType(memory_type)
                entry = self.memory.add_memory(
                    memory_type=mem_type,
                    title=title,
                    content=content,
                    tags=tags,
                    project_id=project_id
                )
                
                return ToolResult(
                    success=True, 
                    content=f"Memory stored: [{mem_type.value}] {title} (ID: {entry.id})"
                )
            
            elif action == "search":
                if not query:
                    return ToolResult(success=False, error="Query is required for 'search'")
                
                mem_type = MemoryType(memory_type) if memory_type else None
                memories = self.memory.search_memories(
                    query=query,
                    project_id=project_id,
                    memory_type=mem_type,
                    limit=limit
                )
                
                if not memories:
                    return ToolResult(success=True, content=f"No memories found for '{query}'")
                
                lines = [f"# Search Results: '{query}'", ""]
                for m in memories:
                    lines.append(f"## [{m.memory_type.value}] {m.title}")
                    lines.append(m.content)
                    if m.tags:
                        lines.append(f"*Tags: {', '.join(m.tags)}*")
                    lines.append("")
                
                return ToolResult(success=True, content="\n".join(lines))
            
            elif action == "list":
                mem_type = MemoryType(memory_type) if memory_type else None
                memories = self.memory.get_memories(
                    project_id=project_id,
                    memory_type=mem_type,
                    limit=limit
                )
                
                if not memories:
                    return ToolResult(success=True, content="No memories stored yet.")
                
                lines = ["# Recent Memories", ""]
                for m in memories:
                    lines.append(f"- **[{m.memory_type.value}]** {m.title}")
                
                return ToolResult(success=True, content="\n".join(lines))
            
            elif action == "export":
                context = self.memory.export_for_llm(
                    project_id=project_id,
                    format="markdown"
                )
                return ToolResult(success=True, content=context)
            
            elif action == "set_project":
                if not project_id or not title:
                    return ToolResult(
                        success=False,
                        error="project_id and title are required for 'set_project'"
                    )
                
                self.memory.create_project(
                    project_id=project_id,
                    title=title,
                    goal=content or ""
                )
                
                return ToolResult(
                    success=True,
                    content=f"Project context set: {title} ({project_id})"
                )
            
            else:
                return ToolResult(success=False, error=f"Unknown action: {action}")
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ============== Enhanced Bash Tool ==============

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
        self.memory = get_project_memory()
    
    def run(self, command: str, purpose: str = None, working_dir: str = None,
            timeout: int = 60, capture_learning: bool = False) -> ToolResult:
        # Execute the command
        result = self.exec_tool.run(
            command=command,
            working_directory=working_dir,
            timeout=timeout
        )
        
        # If successful and learning is requested, store it
        if result.get("success") and capture_learning and purpose:
            try:
                self.memory.add_memory(
                    memory_type=MemoryType.KEY_STEP,
                    title=purpose,
                    content=f"Command: `{command}`\n\nPurpose: {purpose}",
                    tags=["bash", "command"]
                )
            except Exception:
                pass  # Don't fail the tool if memory storage fails
        
        return result


# ============== Think Tool ==============

class ThinkParams(BaseModel):
    thought: str = Field(
        ...,
        description="Your reasoning, analysis, or thought process"
    )
    conclusion: Optional[str] = Field(
        None,
        description="The conclusion reached after thinking"
    )


class ThinkTool(BaseTool):
    """
    A sophisticated tool for deep reasoning, strategic planning, and complex problem decomposition.
    Use this to architect multi-step solutions, analyze data dependencies, and identify 
    potential risks before executing actions.
    """
    
    name = "think"
    description = (
        "Advanced reasoning and planning tool. Use this for deep analysis, "
        "decomposing complex queries into actionable sub-tasks, and "
        "formulating a robust execution strategy before taking action."
    )
    args_schema = ThinkParams
    
    def run(self, thought: str, conclusion: str = None) -> ToolResult:
        # Structure the output for better clarity in the agent's context and history
        formatted_plan = f"### 🧠 Deep Analysis & Reasoning\n{thought}"
        
        if conclusion:
            formatted_plan += f"\n\n### 📋 Strategic Execution Roadmap\n{conclusion}"
            
        return ToolResult(
            success=True, 
            content=formatted_plan
        )


# ============== Tool Registration ==============

def get_smart_agent_tools() -> List[BaseTool]:
    """Get all tools for the Smart Agent."""
    return [
        DateTimeTool(),
        NotesTool(),
        MemoryTool(),
        SmartBashTool(),
        ThinkTool()
    ]


def get_smart_agent_tool_schemas() -> List[Dict[str, Any]]:
    """Get schemas for all Smart Agent tools."""
    return [tool.schema for tool in get_smart_agent_tools()]
