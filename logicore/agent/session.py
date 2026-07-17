from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class RecoveryState:
    """Tracks recovery attempts within the current session."""
    
    # Per-turn recovery flags (reset each turn)
    has_retried_429: bool = False
    context_compression_attempted: bool = False
    credential_rotation_attempted: bool = False
    format_recovery_attempted: bool = False
    thinking_signature_retry_attempted: bool = False
    image_shrink_retry_attempted: bool = False
    multimodal_content_retry_attempted: bool = False
    invalid_encrypted_content_retry_attempted: bool = False
    rate_limit_retry_attempted: bool = False
    transient_error_retry_attempted: bool = False
    max_output_tokens_escalated: bool = False
    
    # Session-level counters
    total_recovery_attempts: int = 0
    successful_recoveries: int = 0
    failed_recoveries: int = 0
    
    # Recovery history for pattern detection
    recovery_history: List[Dict[str, Any]] = field(default_factory=list)
    
    def reset_for_turn(self):
        """Reset per-turn flags for a new turn."""
        self.has_retried_429 = False
        self.context_compression_attempted = False
        self.credential_rotation_attempted = False
        self.format_recovery_attempted = False
        self.thinking_signature_retry_attempted = False
        self.image_shrink_retry_attempted = False
        self.multimodal_content_retry_attempted = False
        self.invalid_encrypted_content_retry_attempted = False
        self.rate_limit_retry_attempted = False
        self.transient_error_retry_attempted = False
        self.max_output_tokens_escalated = False
    
    def record_recovery(self, recovery_type: str, success: bool, detail: Optional[str] = None):
        """Record a recovery attempt."""
        self.total_recovery_attempts += 1
        if success:
            self.successful_recoveries += 1
        else:
            self.failed_recoveries += 1
        
        self.recovery_history.append({
            "type": recovery_type,
            "success": success,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        })


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
        self.recovery_state = RecoveryState()
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
