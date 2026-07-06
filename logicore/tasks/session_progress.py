"""
Session progress writer that generates plan.md and progress.md files.

Creates markdown files for each session to track:
- plan.md: Task list and dependencies (static view)
- progress.md: Live activity feed with timestamps

Files are written to {workspace_root}/.logicore/sessions/{session_id}/
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from logicore.tasks.models import Task, TaskStatus


class SessionProgressWriter:
    """
    Writes plan.md and progress.md files for a session.
    
    These files provide human-readable session progress that can be:
    - Read by the agent itself for context
    - Viewed by users in a file browser
    - Used for debugging and monitoring
    - Shared between agents in multi-agent setups
    """
    
    def __init__(
        self,
        workspace_root: str,
        session_id: str = "default",
        auto_write: bool = True,
    ):
        """
        Initialize the progress writer.
        
        Args:
            workspace_root: Root directory for the project
            session_id: Session identifier for file isolation
            auto_write: Automatically write files on changes
        """
        self.workspace_root = Path(workspace_root)
        self.session_id = session_id
        self.auto_write = auto_write
        
        # Session directory for progress files
        self._session_dir = self.workspace_root / ".logicore" / "sessions" / session_id
        self._plan_path = self._session_dir / "plan.md"
        self._progress_path = self._session_dir / "progress.md"
        
        # Activity log for progress.md
        self._activities: List[Dict[str, Any]] = []
        self._start_time = datetime.now()
        
        # Ensure directory exists
        if auto_write:
            self._session_dir.mkdir(parents=True, exist_ok=True)
    
    def write_plan(self, tasks: List[Task]) -> str:
        """
        Write plan.md with task list and dependencies.
        
        Args:
            tasks: List of Task objects to include
            
        Returns:
            Path to the written file
        """
        lines = [
            f"# Session Plan",
            f"",
            f"**Session ID:** `{self.session_id}`",
            f"**Created:** {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"---",
            f"",
        ]
        
        if not tasks:
            lines.append("*No tasks created yet.*")
        else:
            # Group by status
            status_groups = {
                TaskStatus.PENDING: [],
                TaskStatus.IN_PROGRESS: [],
                TaskStatus.COMPLETED: [],
                TaskStatus.FAILED: [],
            }
            
            for task in tasks:
                status_groups[task.status].append(task)
            
            # Summary
            total = len(tasks)
            completed = len(status_groups[TaskStatus.COMPLETED])
            in_progress = len(status_groups[TaskStatus.IN_PROGRESS])
            pending = len(status_groups[TaskStatus.PENDING])
            failed = len(status_groups[TaskStatus.FAILED])
            
            lines.extend([
                f"## Summary",
                f"",
                f"| Status | Count |",
                f"|--------|-------|",
                f"| Completed | {completed}/{total} |",
                f"| In Progress | {in_progress}/{total} |",
                f"| Pending | {pending}/{total} |",
                f"| Failed | {failed}/{total} |",
                f"",
                f"---",
                f"",
            ])
            
            # In Progress tasks
            if status_groups[TaskStatus.IN_PROGRESS]:
                lines.extend([
                    f"## In Progress",
                    f"",
                ])
                for task in status_groups[TaskStatus.IN_PROGRESS]:
                    owner = f" ({task.owner})" if task.owner else ""
                    active = f" - *{task.active_form}*" if task.active_form else ""
                    lines.append(f"- **#{task.id}** {task.subject}{owner}{active}")
                    if task.description:
                        lines.append(f"  {task.description[:100]}")
                    if task.blocked_by:
                        lines.append(f"  Blocked by: {', '.join(f'#{b}' for b in task.blocked_by)}")
                lines.append("")
            
            # Pending tasks
            if status_groups[TaskStatus.PENDING]:
                lines.extend([
                    f"## Pending",
                    f"",
                ])
                for task in status_groups[TaskStatus.PENDING]:
                    blocked = ""
                    if task.blocked_by:
                        blocked = f" (blocked by: {', '.join(f'#{b}' for b in task.blocked_by)})"
                    lines.append(f"- **#{task.id}** {task.subject}{blocked}")
                lines.append("")
            
            # Completed tasks
            if status_groups[TaskStatus.COMPLETED]:
                lines.extend([
                    f"## Completed",
                    f"",
                ])
                for task in status_groups[TaskStatus.COMPLETED]:
                    completed_at = ""
                    if task.completed_at:
                        completed_at = f" - {task.completed_at.strftime('%H:%M:%S')}"
                    lines.append(f"- ~~**#{task.id}** {task.subject}~~{completed_at}")
                lines.append("")
            
            # Failed tasks
            if status_groups[TaskStatus.FAILED]:
                lines.extend([
                    f"## Failed",
                    f"",
                ])
                for task in status_groups[TaskStatus.FAILED]:
                    lines.append(f"- **#{task.id}** {task.subject} - *FAILED*")
                    if task.description:
                        lines.append(f"  {task.description[:100]}")
                lines.append("")
        
        content = "\n".join(lines)
        
        if self.auto_write:
            self._plan_path.write_text(content, encoding="utf-8")
        
        return str(self._plan_path)
    
    def record_activity(
        self,
        tool_name: str,
        description: str = "",
        success: bool = True,
        task_id: Optional[str] = None,
    ) -> None:
        """
        Record a tool activity for progress.md.
        
        Args:
            tool_name: Name of the tool executed
            description: Description of what was done
            success: Whether the tool call succeeded
            task_id: Optional task ID this activity is related to
        """
        self._activities.append({
            "timestamp": datetime.now(),
            "tool_name": tool_name,
            "description": description,
            "success": success,
            "task_id": task_id,
        })
        
        if self.auto_write:
            self.write_progress()
    
    def write_progress(self) -> str:
        """
        Write progress.md with activity feed.
        
        Returns:
            Path to the written file
        """
        elapsed = datetime.now() - self._start_time
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        
        lines = [
            f"# Session Progress",
            f"",
            f"**Session ID:** `{self.session_id}`",
            f"**Started:** {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Elapsed:** {minutes}m {seconds}s",
            f"**Activities:** {len(self._activities)}",
            f"",
            f"---",
            f"",
        ]
        
        if not self._activities:
            lines.append("*No activity yet.*")
        else:
            # Last 50 activities (most recent first)
            recent = list(reversed(self._activities[-50:]))
            
            lines.extend([
                f"## Recent Activity",
                f"",
            ])
            
            for activity in recent:
                ts = activity["timestamp"].strftime("%H:%M:%S")
                icon = "+" if activity["success"] else "x"
                task_ref = f" [#{activity['task_id']}]" if activity.get("task_id") else ""
                desc = f" - {activity['description']}" if activity["description"] else ""
                lines.append(f"- `{ts}` [{icon}] **{activity['tool_name']}**{task_ref}{desc}")
            
            lines.append("")
            
            # Statistics
            tool_counts = {}
            success_count = 0
            fail_count = 0
            for a in self._activities:
                tool_counts[a["tool_name"]] = tool_counts.get(a["tool_name"], 0) + 1
                if a["success"]:
                    success_count += 1
                else:
                    fail_count += 1
            
            lines.extend([
                f"## Statistics",
                f"",
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| Total Calls | {len(self._activities)} |",
                f"| Successful | {success_count} |",
                f"| Failed | {fail_count} |",
                f"",
                f"### Tool Usage",
                f"",
                f"| Tool | Count |",
                f"|------|-------|",
            ])
            
            for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                lines.append(f"| {tool} | {count} |")
        
        content = "\n".join(lines)
        
        if self.auto_write:
            self._progress_path.write_text(content, encoding="utf-8")
        
        return str(self._progress_path)
    
    def get_plan_path(self) -> str:
        """Get the path to plan.md."""
        return str(self._plan_path)
    
    def get_progress_path(self) -> str:
        """Get the path to progress.md."""
        return str(self._progress_path)
    
    def clear(self) -> None:
        """Clear all activities and reset."""
        self._activities.clear()
        self._start_time = datetime.now()
        
        if self.auto_write:
            self.write_progress()
