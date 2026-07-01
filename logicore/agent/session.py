from typing import List, Dict, Any
from datetime import datetime


class AgentSession:
    """Represents a conversation session."""
    def __init__(self, session_id: str, system_message: str):
        self.session_id = session_id
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.metadata: Dict[str, Any] = {}
        self.files: Dict[str, str] = {} # VFS: Filename -> Content
    
    def add_message(self, message: Dict[str, Any]):
        self.messages.append(message)
        self.last_activity = datetime.now()
    
    def clear_history(self, keep_system: bool = True):
        if keep_system:
            self.messages = [msg for msg in self.messages if msg.get('role') == 'system']
        else:
            self.messages = []
        self.last_activity = datetime.now()
