"""
Session progress writer that generates plan.md and progress.md files.

Creates markdown files for each session to track:
- plan.md: Task list and dependencies (static view)
- progress.md: Live activity feed with timestamps

Files are written to settings.paths.sessions_dir/{session_id}/ (config-controlled root)
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
    
    Features:
    - Automatic session isolation (each session has its own files)
    - Rich task visualization with status indicators
    - Activity feed with timing and statistics
    - Summary sections for quick overview
    """
    
    def __init__(
        self,
        workspace_root: str,
        session_id: str = "default",
        auto_write: bool = True,
        session_name: str = None,
        session_tags: Dict[str, str] = None,
    ):
        """
        Initialize the progress writer.
        
        Args:
            workspace_root: Root directory for the project
            session_id: Session identifier for file isolation
            auto_write: Automatically write files on changes
            session_name: Optional human-readable session name
            session_tags: Optional tags for session metadata
        """
        self.workspace_root = Path(workspace_root)
        self.session_id = session_id
        self.session_name = session_name or session_id
        self.session_tags = session_tags or {}
        self.auto_write = auto_write
        
        # Session directory for progress files (under config-controlled root)
        self._session_dir = Path(workspace_root) / session_id
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
        
        Creates a comprehensive plan document with:
        - Session metadata and overview
        - Task status summary with progress indicators
        - Detailed task breakdown by status
        - Dependency visualization
        - Timeline and estimates
        
        Args:
            tasks: List of Task objects to include
            
        Returns:
            Path to the written file
        """
        lines = [
            f"# Session Plan",
            f"",
            f"## Overview",
            f"",
            f"| Property | Value |",
            f"|----------|-------|",
            f"| Session ID | `{self.session_id}` |",
            f"| Session Name | {self.session_name} |",
            f"| Created | {self._start_time.strftime('%Y-%m-%d %H:%M:%S')} |",
            f"| Last Updated | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
        ]
        
        # Add tags if present
        if self.session_tags:
            for key, value in self.session_tags.items():
                lines.append(f"| {key} | {value} |")
        
        lines.extend([
            f"",
            f"---",
            f"",
        ])
        
        if not tasks:
            lines.extend([
                "*No tasks created yet.*",
                "",
                "**Next Steps:**",
                "- Use `task_create` tool to add tasks to this session",
                "- Tasks will be tracked and organized here automatically",
            ])
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
            
            # Summary with progress bar
            total = len(tasks)
            completed = len(status_groups[TaskStatus.COMPLETED])
            in_progress = len(status_groups[TaskStatus.IN_PROGRESS])
            pending = len(status_groups[TaskStatus.PENDING])
            failed = len(status_groups[TaskStatus.FAILED])
            progress_percent = int((completed / total) * 100) if total > 0 else 0
            
            # Create progress bar
            bar_length = 20
            filled = int(bar_length * progress_percent / 100)
            progress_bar = "█" * filled + "░" * (bar_length - filled)
            
            lines.extend([
                f"## Progress Summary",
                f"",
                f"```",
                f"[{progress_bar}] {progress_percent}% Complete",
                f"```",
                f"",
                f"| Status | Count | Percentage |",
                f"|--------|-------|------------|",
                f"| ✅ Completed | {completed}/{total} | {int((completed/total)*100) if total > 0 else 0}% |",
                f"| 🔄 In Progress | {in_progress}/{total} | {int((in_progress/total)*100) if total > 0 else 0}% |",
                f"| ⏳ Pending | {pending}/{total} | {int((pending/total)*100) if total > 0 else 0}% |",
                f"| ❌ Failed | {failed}/{total} | {int((failed/total)*100) if total > 0 else 0}% |",
                f"",
                f"---",
                f"",
            ])
            
            # In Progress tasks (highest priority visibility)
            if status_groups[TaskStatus.IN_PROGRESS]:
                lines.extend([
                    f"## 🔄 In Progress ({len(status_groups[TaskStatus.IN_PROGRESS])})",
                    f"",
                ])
                for task in status_groups[TaskStatus.IN_PROGRESS]:
                    owner = f" | **Owner:** {task.owner}" if task.owner else ""
                    active = f" | *{task.active_form}*" if task.active_form else ""
                    created = task.created_at.strftime('%H:%M') if task.created_at else ""
                    
                    lines.append(f"### Task #{task.id}: {task.subject}")
                    lines.append(f"- **Status:** 🔄 In Progress{owner}{active}")
                    if created:
                        lines.append(f"- **Started:** {created}")
                    
                    if task.description:
                        # Truncate long descriptions but keep meaningful content
                        desc = task.description[:200]
                        if len(task.description) > 200:
                            desc += "..."
                        lines.append(f"- **Description:** {desc}")
                    
                    if task.blocked_by:
                        lines.append(f"- **Blocked by:** {', '.join(f'#{b}' for b in task.blocked_by)}")
                    
                    if task.blocks:
                        lines.append(f"- **Blocks:** {', '.join(f'#{b}' for b in task.blocks)}")
                    
                    lines.append("")
            
            # Pending tasks
            if status_groups[TaskStatus.PENDING]:
                lines.extend([
                    f"## ⏳ Pending ({len(status_groups[TaskStatus.PENDING])})",
                    f"",
                ])
                for task in status_groups[TaskStatus.PENDING]:
                    blocked = ""
                    if task.blocked_by:
                        blocked = f" ⚠️ *(blocked by: {', '.join(f'#{b}' for b in task.blocked_by)})*"
                    
                    lines.append(f"- **#{task.id}** {task.subject}{blocked}")
                    if task.description:
                        desc = task.description[:100]
                        if len(task.description) > 100:
                            desc += "..."
                        lines.append(f"  > {desc}")
                lines.append("")
            
            # Completed tasks
            if status_groups[TaskStatus.COMPLETED]:
                lines.extend([
                    f"## ✅ Completed ({len(status_groups[TaskStatus.COMPLETED])})",
                    f"",
                ])
                for task in status_groups[TaskStatus.COMPLETED]:
                    completed_at = ""
                    if task.completed_at:
                        completed_at = f" ({task.completed_at.strftime('%H:%M:%S')})"
                    lines.append(f"- ~~**#{task.id}** {task.subject}~~{completed_at}")
                lines.append("")
            
            # Failed tasks
            if status_groups[TaskStatus.FAILED]:
                lines.extend([
                    f"## ❌ Failed ({len(status_groups[TaskStatus.FAILED])})",
                    f"",
                ])
                for task in status_groups[TaskStatus.FAILED]:
                    lines.append(f"- **#{task.id}** {task.subject} - *FAILED*")
                    if task.description:
                        desc = task.description[:100]
                        if len(task.description) > 100:
                            desc += "..."
                        lines.append(f"  > {desc}")
                lines.append("")
            
            # Dependency visualization
            tasks_with_deps = [t for t in tasks if t.blocked_by or t.blocks]
            if tasks_with_deps:
                lines.extend([
                    f"## 🔗 Dependencies",
                    f"",
                ])
                for task in tasks_with_deps:
                    deps = []
                    if task.blocked_by:
                        deps.append(f"Waiting for: {', '.join(f'#{b}' for b in task.blocked_by)}")
                    if task.blocks:
                        deps.append(f"Blocks: {', '.join(f'#{b}' for b in task.blocks)}")
                    lines.append(f"- **#{task.id}** {task.subject}: {' | '.join(deps)}")
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
        
        Creates a comprehensive progress document with:
        - Session overview and timing
        - Live activity feed with status indicators
        - Tool usage statistics
        - Performance metrics
        - Error tracking
        
        Returns:
            Path to the written file
        """
        elapsed = datetime.now() - self._start_time
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        
        lines = [
            f"# Session Progress",
            f"",
            f"## Overview",
            f"",
            f"| Property | Value |",
            f"|----------|-------|",
            f"| Session ID | `{self.session_id}` |",
            f"| Session Name | {self.session_name} |",
            f"| Started | {self._start_time.strftime('%Y-%m-%d %H:%M:%S')} |",
            f"| Elapsed | {minutes}m {seconds}s |",
            f"| Total Activities | {len(self._activities)} |",
        ]
        
        # Add tags if present
        if self.session_tags:
            for key, value in self.session_tags.items():
                lines.append(f"| {key} | {value} |")
        
        lines.extend([
            f"",
            f"---",
            f"",
        ])
        
        if not self._activities:
            lines.extend([
                "*No activity yet.*",
                "",
                "**Waiting for tool executions...**",
                "Activities will appear here as the agent works on tasks.",
            ])
        else:
            # Statistics first (quick overview)
            tool_counts = {}
            success_count = 0
            fail_count = 0
            for a in self._activities:
                tool_counts[a["tool_name"]] = tool_counts.get(a["tool_name"], 0) + 1
                if a["success"]:
                    success_count += 1
                else:
                    fail_count += 1
            
            # Success rate calculation
            total_calls = len(self._activities)
            success_rate = int((success_count / total_calls) * 100) if total_calls > 0 else 0
            
            lines.extend([
                f"## 📊 Statistics",
                f"",
                f"```",
                f"Success Rate: {success_rate}% ({success_count}/{total_calls})",
                f"```",
                f"",
                f"| Metric | Value | Status |",
                f"|--------|-------|--------|",
                f"| Total Calls | {total_calls} | - |",
                f"| Successful | {success_count} | {'✅' if success_count > 0 else '-'} |",
                f"| Failed | {fail_count} | {'❌' if fail_count > 0 else '-'} |",
                f"| Success Rate | {success_rate}% | {'✅' if success_rate >= 90 else '⚠️' if success_rate >= 70 else '❌'} |",
                f"",
            ])
            
            # Tool usage with visual indicators
            if tool_counts:
                lines.extend([
                    f"### Tool Usage",
                    f"",
                    f"| Tool | Count | Usage |",
                    f"|------|-------|-------|",
                ])
                
                max_count = max(tool_counts.values()) if tool_counts else 1
                for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                    # Create visual bar
                    bar_length = 10
                    filled = int(bar_length * count / max_count)
                    bar = "█" * filled + "░" * (bar_length - filled)
                    lines.append(f"| {tool} | {count} | {bar} |")
                lines.append("")
            
            # Recent activity (last 30, most recent first)
            recent = list(reversed(self._activities[-30:]))
            
            lines.extend([
                f"## 📝 Recent Activity (Last {len(recent)})",
                f"",
            ])
            
            for activity in recent:
                ts = activity["timestamp"].strftime("%H:%M:%S")
                icon = "✅" if activity["success"] else "❌"
                task_ref = f" [#{activity['task_id']}]" if activity.get("task_id") else ""
                desc = f" - {activity['description']}" if activity["description"] else ""
                
                # Calculate time since start
                activity_elapsed = activity["timestamp"] - self._start_time
                elapsed_str = f"{int(activity_elapsed.total_seconds())}s"
                
                lines.append(f"- `{ts}` ({elapsed_str}) {icon} **{activity['tool_name']}**{task_ref}{desc}")
            
            lines.append("")
            
            # Error details if any failures
            failed_activities = [a for a in self._activities if not a["success"]]
            if failed_activities:
                lines.extend([
                    f"## ⚠️ Failed Activities",
                    f"",
                ])
                for activity in failed_activities[-10:]:  # Last 10 failures
                    ts = activity["timestamp"].strftime("%H:%M:%S")
                    desc = f" - {activity['description']}" if activity["description"] else ""
                    lines.append(f"- `{ts}` **{activity['tool_name']}**{desc}")
                lines.append("")
        
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
