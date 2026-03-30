import json
import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class MemoryType(Enum):
    """Types of memories that can be stored."""
    APPROACH = "approach"      # How to solve a type of problem
    LEARNING = "learning"      # Something learned from interaction
    KEY_STEP = "key_step"      # Important step in a workflow
    PATTERN = "pattern"        # Reusable pattern or template
    PREFERENCE = "preference"  # User/project preferences
    DECISION = "decision"      # Key decisions made
    CONTEXT = "context"        # Project context information


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: Optional[int]
    memory_type: MemoryType
    title: str
    content: str
    tags: List[str]
    project_id: Optional[str]  # None = global memory
    created_at: datetime
    relevance_score: float = 1.0
    usage_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.memory_type.value,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "project_id": self.project_id,
            "created_at": self.created_at.isoformat(),
            "relevance_score": self.relevance_score,
            "usage_count": self.usage_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=data.get("id"),
            memory_type=MemoryType(data["type"]),
            title=data["title"],
            content=data["content"],
            tags=data.get("tags", []),
            project_id=data.get("project_id"),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data["created_at"], str) else data["created_at"],
            relevance_score=data.get("relevance_score", 1.0),
            usage_count=data.get("usage_count", 0)
        )


@dataclass
class ProjectContext:
    """Context about a project."""
    project_id: str
    title: str
    goal: str
    environment: Dict[str, str]  # e.g., {"language": "Python", "framework": "FastAPI"}
    key_files: List[str]
    current_focus: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "goal": self.goal,
            "environment": self.environment,
            "key_files": self.key_files,
            "current_focus": self.current_focus,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class ProjectMemory:
    """
    A pluggable memory extension for agents.
    
    Features:
    - Store approaches, learnings, and key steps
    - Project-wise or global scope
    - Export to JSON/Markdown for LLM context
    - Relevance-based retrieval
    """
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "user_data")
            os.makedirs(data_dir, exist_ok=True)
            self.db_path = os.path.join(data_dir, "project_memory.db")
        else:
            self.db_path = db_path
        
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Projects table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                goal TEXT,
                environment TEXT,
                key_files TEXT,
                current_focus TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Memories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                project_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                relevance_score REAL DEFAULT 1.0,
                usage_count INTEGER DEFAULT 0,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            )
        """)
        
        # Full-text search index
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                title, content, tags,
                content='memories',
                content_rowid='id'
            )
        """)
        
        # Triggers to keep FTS in sync
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, title, content, tags) 
                VALUES (new.id, new.title, new.content, new.tags);
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, title, content, tags) 
                VALUES ('delete', old.id, old.title, old.content, old.tags);
            END
        """)
        
        conn.commit()
        conn.close()
    
    # --- Project Management ---
    
    def create_project(self, project_id: str, title: str, goal: str = "", 
                       environment: Dict[str, str] = None, 
                       key_files: List[str] = None) -> ProjectContext:
        """Create a new project context."""
        now = datetime.now()
        project = ProjectContext(
            project_id=project_id,
            title=title,
            goal=goal,
            environment=environment or {},
            key_files=key_files or [],
            current_focus=None,
            created_at=now,
            updated_at=now
        )
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO projects 
                (project_id, title, goal, environment, key_files, current_focus, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project.project_id, project.title, project.goal,
                json.dumps(project.environment), json.dumps(project.key_files),
                project.current_focus, project.created_at, project.updated_at
            ))
            conn.commit()
        finally:
            conn.close()
        
        return project
    
    def get_project(self, project_id: str) -> Optional[ProjectContext]:
        """Get project context by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            row = cursor.fetchone()
            if row:
                return ProjectContext(
                    project_id=row["project_id"],
                    title=row["title"],
                    goal=row["goal"] or "",
                    environment=json.loads(row["environment"] or "{}"),
                    key_files=json.loads(row["key_files"] or "[]"),
                    current_focus=row["current_focus"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now()
                )
            return None
        finally:
            conn.close()
    
    def update_project_focus(self, project_id: str, focus: str):
        """Update the current focus of a project."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE projects SET current_focus = ?, updated_at = ? 
                WHERE project_id = ?
            """, (focus, datetime.now(), project_id))
            conn.commit()
        finally:
            conn.close()
    
    def list_projects(self) -> List[ProjectContext]:
        """List all projects."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            return [
                ProjectContext(
                    project_id=row["project_id"],
                    title=row["title"],
                    goal=row["goal"] or "",
                    environment=json.loads(row["environment"] or "{}"),
                    key_files=json.loads(row["key_files"] or "[]"),
                    current_focus=row["current_focus"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now()
                )
                for row in rows
            ]
        finally:
            conn.close()
    
    # --- Memory Management ---
    
    def add_memory(self, memory_type: MemoryType, title: str, content: str,
                   tags: List[str] = None, project_id: str = None) -> MemoryEntry:
        """Add a new memory entry."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO memories (memory_type, title, content, tags, project_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                memory_type.value, title, content,
                json.dumps(tags or []), project_id, datetime.now()
            ))
            memory_id = cursor.lastrowid
            conn.commit()
            
            return MemoryEntry(
                id=memory_id,
                memory_type=memory_type,
                title=title,
                content=content,
                tags=tags or [],
                project_id=project_id,
                created_at=datetime.now()
            )
        finally:
            conn.close()
    
    def search_memories(self, query: str, project_id: str = None, 
                        memory_type: MemoryType = None, limit: int = 10) -> List[MemoryEntry]:
        """Search memories using full-text search."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            # Build query with filters
            sql = """
                SELECT m.*, bm25(memories_fts) as rank
                FROM memories m
                JOIN memories_fts ON m.id = memories_fts.rowid
                WHERE memories_fts MATCH ?
            """
            params = [query]
            
            if project_id:
                sql += " AND (m.project_id = ? OR m.project_id IS NULL)"
                params.append(project_id)
            
            if memory_type:
                sql += " AND m.memory_type = ?"
                params.append(memory_type.value)
            
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            # Update usage count for retrieved memories
            for row in rows:
                cursor.execute(
                    "UPDATE memories SET usage_count = usage_count + 1 WHERE id = ?",
                    (row["id"],)
                )
            conn.commit()
            
            return [
                MemoryEntry(
                    id=row["id"],
                    memory_type=MemoryType(row["memory_type"]),
                    title=row["title"],
                    content=row["content"],
                    tags=json.loads(row["tags"] or "[]"),
                    project_id=row["project_id"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                    relevance_score=row["relevance_score"],
                    usage_count=row["usage_count"]
                )
                for row in rows
            ]
        except sqlite3.OperationalError:
            # FTS query failed, fall back to LIKE search
            sql = """
                SELECT * FROM memories 
                WHERE (title LIKE ? OR content LIKE ?)
            """
            pattern = f"%{query}%"
            params = [pattern, pattern]
            
            if project_id:
                sql += " AND (project_id = ? OR project_id IS NULL)"
                params.append(project_id)
            
            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)
            
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            return [
                MemoryEntry(
                    id=row["id"],
                    memory_type=MemoryType(row["memory_type"]),
                    title=row["title"],
                    content=row["content"],
                    tags=json.loads(row["tags"] or "[]"),
                    project_id=row["project_id"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                    relevance_score=row["relevance_score"],
                    usage_count=row["usage_count"]
                )
                for row in rows
            ]
        finally:
            conn.close()
    
    def get_memories(self, project_id: str = None, memory_type: MemoryType = None,
                     limit: int = 50) -> List[MemoryEntry]:
        """Get memories with optional filters."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            sql = "SELECT * FROM memories WHERE 1=1"
            params = []
            
            if project_id:
                sql += " AND (project_id = ? OR project_id IS NULL)"
                params.append(project_id)
            
            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)
            
            sql += " ORDER BY relevance_score DESC, usage_count DESC, created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            return [
                MemoryEntry(
                    id=row["id"],
                    memory_type=MemoryType(row["memory_type"]),
                    title=row["title"],
                    content=row["content"],
                    tags=json.loads(row["tags"] or "[]"),
                    project_id=row["project_id"],
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                    relevance_score=row["relevance_score"],
                    usage_count=row["usage_count"]
                )
                for row in rows
            ]
        finally:
            conn.close()
    
    def update_memory_relevance(self, memory_id: int, score: float):
        """Update the relevance score of a memory."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE memories SET relevance_score = ? WHERE id = ?",
                (score, memory_id)
            )
            conn.commit()
        finally:
            conn.close()
    
    def delete_memory(self, memory_id: int):
        """Delete a memory entry."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
        finally:
            conn.close()
    
    # --- Export ---
    
    def export_for_llm(self, project_id: str = None, format: str = "markdown",
                       include_global: bool = True) -> str:
        """
        Export memories in a format suitable for LLM context injection.
        
        Args:
            project_id: Optional project to filter by
            format: 'markdown' or 'json'
            include_global: Whether to include global (non-project) memories
        
        Returns:
            Formatted string suitable for LLM context
        """
        memories = self.get_memories(project_id=project_id, limit=100)
        
        if not include_global and project_id:
            memories = [m for m in memories if m.project_id == project_id]
        
        if format == "json":
            return json.dumps([m.to_dict() for m in memories], indent=2, default=str)
        
        # Markdown format
        lines = ["# Agent Memory Context", ""]
        
        # Group by type
        by_type: Dict[MemoryType, List[MemoryEntry]] = {}
        for m in memories:
            if m.memory_type not in by_type:
                by_type[m.memory_type] = []
            by_type[m.memory_type].append(m)
        
        type_order = [
            MemoryType.APPROACH, MemoryType.PATTERN, MemoryType.LEARNING,
            MemoryType.KEY_STEP, MemoryType.DECISION, MemoryType.PREFERENCE,
            MemoryType.CONTEXT
        ]
        
        for mem_type in type_order:
            if mem_type in by_type:
                lines.append(f"## {mem_type.value.title()}s")
                lines.append("")
                for m in by_type[mem_type]:
                    lines.append(f"### {m.title}")
                    lines.append(m.content)
                    if m.tags:
                        lines.append(f"*Tags: {', '.join(m.tags)}*")
                    lines.append("")
        
        return "\n".join(lines)
    
    def export_project_context(self, project_id: str) -> str:
        """Export full project context for LLM injection."""
        project = self.get_project(project_id)
        if not project:
            return ""
        
        memories = self.export_for_llm(project_id=project_id, format="markdown")
        
        lines = [
            "# Project Context",
            f"**Project:** {project.title}",
            f"**Goal:** {project.goal}",
            ""
        ]
        
        if project.environment:
            lines.append("## Environment")
            for k, v in project.environment.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")
        
        if project.key_files:
            lines.append("## Key Files")
            for f in project.key_files:
                lines.append(f"- `{f}`")
            lines.append("")
        
        if project.current_focus:
            lines.append(f"## Current Focus")
            lines.append(project.current_focus)
            lines.append("")
        
        lines.append(memories)
        
        return "\n".join(lines)


# Global instance for easy access
_project_memory: Optional[ProjectMemory] = None

def get_project_memory() -> ProjectMemory:
    """Get or create the global ProjectMemory instance."""
    global _project_memory
    if _project_memory is None:
        _project_memory = ProjectMemory()
    return _project_memory
