"""
Session Manager for logicore.
Uses the new storage interface for persistence.
"""
import os
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Any, Optional
from contextlib import contextmanager


class SessionStorage:
    """
    Lightweight SQLite storage for sessions.
    This is a minimal implementation for CLI/TUI usage.
    For web usage, backend.services.storage provides more features.
    """
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Default: ui/scratchy_users.db
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "ui", "scratchy_users.db")
        
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._ensure_tables()
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _ensure_tables(self):
        """Ensure required tables exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP,
                    last_activity TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_state (
                    session_id TEXT,
                    key TEXT,
                    value TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (session_id, key)
                )
            """)
            
            conn.commit()
    
    def create_session(self, session_id: str, metadata: Dict = None):
        """Create a new session."""
        now = datetime.now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at, last_activity, metadata) VALUES (?, ?, ?, ?)",
                (session_id, now, now, json.dumps(metadata or {}))
            )
            conn.commit()
    
    def update_session_activity(self, session_id: str):
        """Update session last activity."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET last_activity = ? WHERE session_id = ?",
                (datetime.now(), session_id)
            )
            conn.commit()
    
    def update_session_metadata(self, session_id: str, metadata: Dict):
        """Update session metadata."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get existing metadata
            cursor.execute("SELECT metadata FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            
            existing = {}
            if row and row["metadata"]:
                try:
                    existing = json.loads(row["metadata"])
                except Exception:
                    pass
            
            existing.update(metadata)
            
            cursor.execute(
                "UPDATE sessions SET metadata = ? WHERE session_id = ?",
                (json.dumps(existing), session_id)
            )
            conn.commit()
    
    def save_state(self, session_id: str, key: str, value: Any):
        """Save a state value."""
        now = datetime.now()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO agent_state (session_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, key, json.dumps(value), now)
            )
            conn.commit()
    
    def load_state(self, session_id: str, key: str) -> Optional[Any]:
        """Load a state value."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM agent_state WHERE session_id = ? AND key = ?",
                (session_id, key)
            )
            row = cursor.fetchone()
            
            if row:
                try:
                    return json.loads(row["value"])
                except Exception:
                    return row["value"]
            return None
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its associated state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Delete from agent_state first (child table)
            cursor.execute("DELETE FROM agent_state WHERE session_id = ?", (session_id,))
            # Delete from sessions table
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT session_id, created_at, last_activity, metadata FROM sessions ORDER BY last_activity DESC"
            )
            rows = cursor.fetchall()
            
            sessions = []
            for row in rows:
                # Get message count
                msg_cursor = cursor.execute(
                    "SELECT value FROM agent_state WHERE session_id = ? AND key = 'messages'",
                    (row["session_id"],)
                )
                msg_row = msg_cursor.fetchone()
                msg_count = 0
                if msg_row:
                    try:
                        msgs = json.loads(msg_row["value"])
                        msg_count = len([m for m in msgs if isinstance(m, dict) and m.get("role") == "user"])
                    except Exception:
                        pass
                
                sessions.append({
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "last_activity": row["last_activity"],
                    "metadata": row["metadata"],
                    "message_count": msg_count
                })
            
            return sessions


class SessionManager:
    """
    Manages chat sessions using SQLite storage.
    """
    
    def __init__(self, storage: SessionStorage = None):
        if storage is None:
            self.storage = SessionStorage()
        else:
            self.storage = storage
            
    def save_session(self, session_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any] = None):
        """Save session messages to persistent storage."""
        if not messages:
            return

        # Ensure session exists
        if not self.storage.load_state(session_id, "messages"):
            self.storage.create_session(session_id, metadata=metadata or {"source": "logicore_cli"})
             
        # Update activity timestamp
        self.storage.update_session_activity(session_id)
        
        # Update metadata if provided
        if metadata:
            self.storage.update_session_metadata(session_id, metadata)
        
        # Save messages
        self.storage.save_state(session_id, "messages", messages)

    def load_session(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        """Load session messages from persistent storage."""
        messages = self.storage.load_state(session_id, "messages")
        if messages is None:
            return []
        return messages
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all available sessions."""
        sessions = self.storage.list_sessions()
        
        # Parse metadata to extract title, provider, model
        for s in sessions:
            if s.get('metadata'):
                try:
                    meta = json.loads(s['metadata'])
                    s['title'] = meta.get('title')
                    s['provider'] = meta.get('provider')
                    s['model'] = meta.get('model')
                    s['model_type'] = meta.get('model_type')
                except Exception:
                    pass
        
        # Filter out sessions with no messages
        return [s for s in sessions if s.get('message_count', 0) > 0]

    def update_session_title(self, session_id: str, title: str):
        """Update the title of a session."""
        self.storage.update_session_metadata(session_id, {"title": title})
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        self.storage.save_state(session_id, "messages", [])
        return True
    
    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""
        return self.storage.load_state(session_id, "messages") is not None
