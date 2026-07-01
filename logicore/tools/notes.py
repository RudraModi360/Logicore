from typing import Optional, Literal, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
import json
import os


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
