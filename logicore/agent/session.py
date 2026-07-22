from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field


class AgentSession:
    """Represents a conversation session."""
    def __init__(self, session_id: str, system_message: str):
        self.session_id = session_id
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.metadata: Dict[str, Any] = {}
        self.files: Dict[str, str] = {} # VFS: Filename -> Content
        
        # Structured state for self-healing
        self.corrections_made: List[Dict[str, Any]] = []
        self.explored_topics: List[str] = []
        self.tool_results_history: List[Dict[str, Any]] = []
    
    def add_message(self, message: Dict[str, Any]):
        self.messages.append(message)
        self.last_activity = datetime.now()
    
    def add_correction(self, correction_type: str, original: str, corrected: str, context: Optional[str] = None):
        """Record a user correction for learning."""
        self.corrections_made.append({
            "type": correction_type,
            "original": original,
            "corrected": corrected,
            "context": context,
            "timestamp": datetime.now().isoformat(),
        })
    
    def add_explored_topic(self, topic: str):
        """Track topics that have been explored in this session."""
        if topic not in self.explored_topics:
            self.explored_topics.append(topic)
    
    def add_tool_result(self, tool_name: str, success: bool, result_summary: str, args_hash: Optional[str] = None):
        """Track tool execution results for pattern detection."""
        self.tool_results_history.append({
            "tool_name": tool_name,
            "success": success,
            "result_summary": result_summary[:200],  # Truncate for storage
            "args_hash": args_hash,
            "timestamp": datetime.now().isoformat(),
        })
    
    def clear_history(self, keep_system: bool = True):
        if keep_system:
            self.messages = [msg for msg in self.messages if msg.get('role') == 'system']
        else:
            self.messages = []
        self.last_activity = datetime.now()
