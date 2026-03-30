import sqlite3
import json
import os
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

class PersistentMemoryStore:
    """
    High-performance persistent storage with connection pooling.
    Uses thread-local connections and optimized queries.
    """
    
    _local = threading.local()
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Default to scratchy/user_data/memory.db
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "user_data")
            os.makedirs(data_dir, exist_ok=True)
            self.db_path = os.path.join(data_dir, "memory.db")
        else:
            self.db_path = db_path
            
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local connection for better performance."""
        if not hasattr(self._local, 'connections'):
            self._local.connections = {}
        
        if self.db_path not in self._local.connections:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent read/write
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-32000")  # 32MB cache
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.connections[self.db_path] = conn
        
        return self._local.connections[self.db_path]
    
    @contextmanager
    def _connection(self):
        """Context manager for database operations with auto-commit."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Enable WAL mode for better concurrent performance
        cursor.execute("PRAGMA journal_mode=WAL")
        
        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP,
                last_activity TIMESTAMP,
                metadata TEXT
            )
        """)

        # Memories table (Long-term memory)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                type TEXT,
                content TEXT,
                timestamp TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            )
        """)

        # Agent State table (Checkpointing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_state (
                session_id TEXT,
                key TEXT,
                value TEXT,
                updated_at TIMESTAMP,
                PRIMARY KEY (session_id, key),
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            )
        """)
        
        # Create indexes for faster lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_state_session ON agent_state(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_activity ON sessions(last_activity DESC)")
        
        conn.commit()
        conn.close()

    def create_session(self, session_id: str, metadata: Dict[str, Any] = None):
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at, last_activity, metadata) VALUES (?, ?, ?, ?)",
                (session_id, datetime.now(), datetime.now(), json.dumps(metadata or {}))
            )

    def delete_session(self, session_id: str):
        """Delete a session and all associated data."""
        with self._connection() as conn:
            cursor = conn.cursor()
            # Delete from agent_state
            cursor.execute("DELETE FROM agent_state WHERE session_id = ?", (session_id,))
            # Delete from memories
            cursor.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
            # Delete session record
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def update_session_metadata(self, session_id: str, updates: Dict[str, Any]):
        """Update specific fields in session metadata."""
        with self._connection() as conn:
            cursor = conn.cursor()
            # First get current metadata
            cursor.execute("SELECT metadata FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                print(f"[Storage] Session {session_id} not found for metadata update")
                return
            
            current_metadata = json.loads(row[0] or '{}')
            current_metadata.update(updates)
            
            # Ensure we're passing a string, not a dict
            serialized_metadata = json.dumps(current_metadata)
            
            cursor.execute(
                "UPDATE sessions SET metadata = ? WHERE session_id = ?",
                (serialized_metadata, session_id)
            )

    def update_session_activity(self, session_id: str):
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET last_activity = ? WHERE session_id = ?",
                (datetime.now(), session_id)
            )

    def add_memory(self, session_id: str, memory_type: str, content: str):
        """
        Add a memory. 
        session_id can be a specific session ID or 'global' for cross-session memories.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO memories (session_id, type, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, memory_type, content, datetime.now())
            )

    def get_memories(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get memories for a specific session AND global memories."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories 
            WHERE session_id = ? OR session_id = 'global'
            ORDER BY timestamp DESC LIMIT ?
            """,
            (session_id, limit)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_state(self, session_id: str, key: str, value: Any):
        """Save arbitrary state (checkpointing)."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_state (session_id, key, value, updated_at) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, key) DO UPDATE SET 
                value=excluded.value, updated_at=excluded.updated_at
                """,
                (session_id, key, json.dumps(value), datetime.now())
            )

    def load_state(self, session_id: str, key: str) -> Optional[Any]:
        """Load arbitrary state."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM agent_state WHERE session_id = ? AND key = ?",
            (session_id, key)
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions ordered by last activity. Uses optimized single-query approach."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Use a single LEFT JOIN query instead of N+1 queries for message counts
        cursor.execute("""
            SELECT 
                s.session_id,
                s.created_at,
                s.last_activity,
                s.metadata,
                a.value as messages_json
            FROM sessions s
            LEFT JOIN agent_state a ON s.session_id = a.session_id AND a.key = 'messages'
            ORDER BY s.last_activity DESC
        """)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            data = dict(row)
            data['id'] = data['session_id']  # Map session_id to id for compatibility
            
            # Parse and merge metadata fields into the session dict
            metadata_str = data.get('metadata')
            if metadata_str:
                try:
                    metadata = json.loads(metadata_str)
                    # Merge metadata fields into the session dict
                    data['title'] = metadata.get('title')
                    data['provider'] = metadata.get('provider')
                    data['model'] = metadata.get('model')
                    data['model_type'] = metadata.get('model_type')
                except:
                    pass
            
            # Calculate message count from the joined data
            msg_count = 0
            messages_json = data.pop('messages_json', None)
            if messages_json:
                try:
                    msgs = json.loads(messages_json)
                    # Count conversation turns: only count user messages
                    msg_count = sum(1 for m in msgs if m.get('role') == 'user')
                except: 
                    pass
            
            data['message_count'] = msg_count
            results.append(data)
            
        return results
