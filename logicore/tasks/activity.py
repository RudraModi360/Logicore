"""
Activity tracking and summarization for task progress.

Tracks rolling window of recent activities and provides
summarized text for UI display.

Inspired by Claude Code's collapseReadSearch pattern:
- Consecutive search/read operations are collapsed
- Activity descriptions are rolled up into summaries
- Recent activities provide context for progress display
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import deque


@dataclass
class ToolActivity:
    """A single tool usage activity."""
    tool_name: str
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_search: bool = False
    is_read: bool = False
    is_write: bool = False
    is_bash: bool = False
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivitySummary:
    """Summarized activity for display."""
    text: str
    search_count: int = 0
    read_count: int = 0
    write_count: int = 0
    bash_count: int = 0
    total_count: int = 0
    last_activity: Optional[str] = None


class ActivityTracker:
    """
    Rolling window activity tracker with summarization.
    
    Tracks recent tool usage activities and provides collapsed
    summaries for UI display.
    
    Features:
    - Rolling window of last N activities
    - Collapsed search/read summaries
    - Activity classification (search, read, write, bash)
    - Last activity description for spinner display
    """
    
    def __init__(self, max_activities: int = 20):
        """
        Initialize activity tracker.
        
        Args:
            max_activities: Maximum number of activities to keep in rolling window
        """
        self.max_activities = max_activities
        self._activities: deque[ToolActivity] = deque(maxlen=max_activities)
    
    def record(
        self,
        tool_name: str,
        description: str = "",
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ToolActivity:
        """
        Record a tool activity.
        
        Args:
            tool_name: Name of the tool used
            description: Human-readable description
            success: Whether the tool call succeeded
            metadata: Additional metadata
            
        Returns:
            The recorded activity
        """
        activity = ToolActivity(
            tool_name=tool_name,
            description=description or f"Used {tool_name}",
            is_search=tool_name in ("search_files", "fast_grep", "grep"),
            is_read=tool_name in ("read_file", "read_document"),
            is_write=tool_name in ("create_file", "edit_file", "write_file"),
            is_bash=tool_name in ("bash", "execute_command"),
            success=success,
            metadata=metadata or {},
        )
        self._activities.append(activity)
        return activity
    
    def get_recent(self, count: int = 5) -> List[ToolActivity]:
        """Get the most recent activities."""
        return list(self._activities)[-count:]
    
    def get_last_activity(self) -> Optional[ToolActivity]:
        """Get the most recent activity."""
        if self._activities:
            return self._activities[-1]
        return None
    
    def get_summary(self) -> ActivitySummary:
        """
        Get a collapsed summary of recent activities.
        
        Collapses consecutive search/read operations into a single summary.
        """
        if not self._activities:
            return ActivitySummary(text="No activity")
        
        activities = list(self._activities)
        
        # Count by type
        search_count = sum(1 for a in activities if a.is_search)
        read_count = sum(1 for a in activities if a.is_read)
        write_count = sum(1 for a in activities if a.is_write)
        bash_count = sum(1 for a in activities if a.is_bash)
        total_count = len(activities)
        
        # Build summary text
        parts = []
        
        if search_count > 0 or read_count > 0:
            # Collapse consecutive search/read
            search_read_count = search_count + read_count
            if search_read_count > 1:
                parts.append(f"Searched for {search_count} patterns, read {read_count} files")
            elif search_count == 1:
                parts.append(f"Searched for a pattern")
            else:
                parts.append(f"Read a file")
        
        if write_count > 0:
            parts.append(f"Modified {write_count} file{'s' if write_count > 1 else ''}")
        
        if bash_count > 0:
            parts.append(f"Ran {bash_count} command{'s' if bash_count > 1 else ''}")
        
        # If no specific type dominated, use last activity
        if not parts and activities:
            parts.append(activities[-1].description)
        
        text = ", ".join(parts) if parts else "Working"
        
        return ActivitySummary(
            text=text,
            search_count=search_count,
            read_count=read_count,
            write_count=write_count,
            bash_count=bash_count,
            total_count=total_count,
            last_activity=activities[-1].description if activities else None,
        )
    
    def clear(self) -> None:
        """Clear all activities."""
        self._activities.clear()
    
    def to_list(self) -> List[Dict[str, Any]]:
        """Convert activities to list of dicts for serialization."""
        return [
            {
                "tool_name": a.tool_name,
                "description": a.description,
                "timestamp": a.timestamp.isoformat(),
                "is_search": a.is_search,
                "is_read": a.is_read,
                "is_write": a.is_write,
                "is_bash": a.is_bash,
                "success": a.success,
            }
            for a in self._activities
        ]
