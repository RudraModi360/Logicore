"""
Attachment System for just-in-time context injection.

Based on Claude Code's attachments.ts pattern:
- Count HUMAN turns (not tool calls or assistant messages)
- Inject on every API call cycle
- Full vs sparse reminders
- Plan mode, verification, and todo reminders

Key insight from Claude Code:
- The tool loop calls getAttachmentMessages on every tool round
- Counting assistant messages would fire reminders every 5 tool calls instead of every 5 human turns
- So we count HUMAN turns only
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime


class AttachmentType(Enum):
    """Types of attachments that can be injected."""
    PLAN_MODE = "plan_mode"
    PLAN_MODE_REENTRY = "plan_mode_reentry"
    PLAN_MODE_EXIT = "plan_mode_exit"
    VERIFY_REMINDER = "verify_reminder"
    TODO_REMINDER = "todo_reminder"


@dataclass
class Attachment:
    """An attachment to be injected into the conversation."""
    type: AttachmentType
    content: str
    is_full: bool = False
    metadata: Optional[Dict[str, Any]] = None


class AttachmentManager:
    """
    Manages just-in-time context injection based on Claude Code's pattern.
    
    Key features:
    1. Counts HUMAN turns (not tool calls)
    2. Injects on every API call cycle
    3. Full vs sparse reminders
    4. Configurable intervals
    """
    
    # Configuration based on Claude Code's PLAN_MODE_ATTACHMENT_CONFIG
    PLAN_CONFIG = {
        "turns_between": 5,  # Inject every 5 human turns
        "full_reminder_every_n": 5,  # Full reminder every 5 attachments
    }
    
    VERIFY_CONFIG = {
        "turns_between": 10,  # Inject every 10 human turns
    }
    
    TODO_CONFIG = {
        "turns_between": 5,  # Inject every 5 human turns
        "pending_threshold": 3,  # Only remind if 3+ pending tasks
    }
    
    def __init__(self):
        """Initialize the attachment manager."""
        self.plan_attachment_count = 0
        self.verify_attachment_count = 0
        self.todo_attachment_count = 0
    
    def count_human_turns(self, messages: List[Dict[str, Any]]) -> int:
        """
        Count HUMAN turns since last attachment.
        
        Based on Claude Code's getPlanModeAttachmentTurnCount():
        - Count only human messages (not meta, not tool results)
        - Stop at last attachment
        
        This is critical - counting assistant messages would fire reminders
        every 5 tool calls instead of every 5 human turns.
        """
        count = 0
        for msg in reversed(messages):
            # Count only human messages (not meta, not tool results)
            role = msg.get("role", "")
            is_meta = msg.get("is_meta", False)
            has_tool_result = msg.get("has_tool_result", False)
            
            if role == "user" and not is_meta and not has_tool_result:
                count += 1
            elif msg.get("type") == "attachment":
                break  # Stop at last attachment
        
        return count
    
    def count_plan_attachments_since_last_exit(self, messages: List[Dict[str, Any]]) -> int:
        """
        Count plan_mode attachments since last plan_mode_exit.
        
        Based on Claude Code's countPlanModeAttachmentsSinceLastExit():
        - Ensures full/sparse cycle resets when exiting plan mode
        - Counts plan_mode attachments since last plan_mode_exit
        """
        count = 0
        for msg in reversed(messages):
            if msg.get("type") == "attachment":
                attachment = msg.get("attachment", {})
                if attachment.get("type") == "plan_mode_exit":
                    break
                if attachment.get("type") == "plan_mode":
                    count += 1
        return count
    
    def get_attachments(
        self,
        messages: List[Dict[str, Any]],
        session_state: Dict[str, Any]
    ) -> List[Attachment]:
        """
        Get context-appropriate attachments based on session state.
        
        Args:
            messages: Conversation messages
            session_state: Current session state including:
                - in_plan_mode: bool
                - plan_file_path: str
                - files_changed: int
                - pending_tasks: int
                - has_plan: bool
        
        Returns:
            List of attachments to inject
        """
        attachments = []
        
        # Plan mode reminders
        if session_state.get("in_plan_mode"):
            human_turns = self.count_human_turns(messages)
            
            if human_turns >= self.PLAN_CONFIG["turns_between"]:
                # Count plan attachments since last exit for full/sparse cycling
                attachment_count = self.count_plan_attachments_since_last_exit(messages) + 1
                
                # Determine full vs sparse (based on Claude Code's pattern)
                is_full = (
                    attachment_count % 
                    self.PLAN_CONFIG["full_reminder_every_n"] == 1
                )
                
                attachments.append(Attachment(
                    type=AttachmentType.PLAN_MODE,
                    content=self._get_plan_mode_content(is_full, session_state),
                    is_full=is_full
                ))
        
        # Verification reminders (based on Claude Code's verify_plan_reminder)
        if session_state.get("files_changed", 0) > 3:
            human_turns = self.count_human_turns(messages)
            if human_turns >= self.VERIFY_CONFIG["turns_between"]:
                self.verify_attachment_count += 1
                attachments.append(Attachment(
                    type=AttachmentType.VERIFY_REMINDER,
                    content=self._get_verify_content(session_state)
                ))
        
        # TODO reminders
        if session_state.get("pending_tasks", 0) >= self.TODO_CONFIG["pending_threshold"]:
            human_turns = self.count_human_turns(messages)
            if human_turns >= self.TODO_CONFIG["turns_between"]:
                self.todo_attachment_count += 1
                attachments.append(Attachment(
                    type=AttachmentType.TODO_REMINDER,
                    content=self._get_todo_content(session_state)
                ))
        
        return attachments
    
    def _get_plan_mode_content(self, is_full: bool, state: Dict[str, Any]) -> str:
        """
        Get plan mode reminder content.
        
        Based on Claude Code's pattern:
        - Full reminder: Complete instructions
        - Sparse reminder: Condensed version
        """
        plan_file = state.get('plan_file_path', 'plan.md')
        
        if is_full:
            return f"""Plan mode active. Plan file: {plan_file}

Follow this workflow:
1. Read relevant code to understand current state
2. Design your approach
3. Write plan to {plan_file}
4. Ask user for clarification if needed (use ask_user_question)
5. Call exit_plan_mode when plan is ready for approval

Remember: Read-only except for the plan file.
You can use read_file, search_files, fast_grep, and list_files.
You CANNOT use file_edit, file_write, or bash (except read-only commands)."""
        else:
            return f"""Plan mode still active. Read-only except plan file ({plan_file}). 
End turns with ask_user_question (for clarifications) or exit_plan_mode (for plan approval)."""
    
    def _get_verify_content(self, state: Dict[str, Any]) -> str:
        """Get verification reminder content."""
        files_changed = state.get('files_changed', 0)
        return f"""You've made {files_changed} file changes. Consider verifying your work:
- Did you test the changes?
- Did you check for edge cases?
- Would you be confident explaining this to someone else?

Use verify_plan_execution to report your verification status."""
    
    def _get_todo_content(self, state: Dict[str, Any]) -> str:
        """Get TODO reminder content."""
        pending = state.get('pending_tasks', 0)
        return f"""You have {pending} pending tasks. Use task_list to see progress and task_next to get the next available task."""
    
    def reset(self):
        """Reset attachment counters."""
        self.plan_attachment_count = 0
        self.verify_attachment_count = 0
        self.todo_attachment_count = 0


def create_system_reminder(content: str) -> Dict[str, Any]:
    """
    Create a system reminder message.
    
    Based on Claude Code's wrapInSystemReminder():
    Wraps content in <system-reminder> tags
    """
    return {
        "role": "user",
        "content": f"<system-reminder>\n{content}\n</system-reminder>",
        "is_meta": True,
        "type": "attachment"
    }


# Global instance for convenience
_attachment_manager: Optional[AttachmentManager] = None


def get_attachment_manager() -> AttachmentManager:
    """Get or create the global attachment manager."""
    global _attachment_manager
    if _attachment_manager is None:
        _attachment_manager = AttachmentManager()
    return _attachment_manager


def get_attachment_messages(
    messages: List[Dict[str, Any]],
    session_state: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Get attachment messages to inject into the conversation.
    
    This is the main entry point - call this on every API call cycle.
    
    Args:
        messages: Conversation messages
        session_state: Current session state
    
    Returns:
        List of system reminder messages to inject
    """
    manager = get_attachment_manager()
    attachments = manager.get_attachments(messages, session_state)
    
    return [create_system_reminder(att.content) for att in attachments]
