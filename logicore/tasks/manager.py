"""
Task lifecycle manager.

Handles the complete task lifecycle:
- Create tasks with subject, description, active form
- Claim tasks (assign ownership)
- Update task status and active form
- Complete tasks (cascade unblock dependents)
- Delete tasks (cascade reference cleanup)
- Get next available task
- Check agent busy status
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from datetime import datetime

from logicore.tasks.models import Task, TaskStatus
from logicore.tasks.store import TaskStore


class TaskManager:
    """
    High-level task lifecycle management.
    
    Wraps TaskStore with business logic for:
    - Claiming with validation (not blocked, not already claimed)
    - Completion with cascade (unblock dependent tasks)
    - Agent busy checking (does agent own other active tasks?)
    - Next task finding (first available unblocked pending task)
    """
    
    def __init__(self, store: TaskStore):
        self.store = store
    
    def create_task(
        self,
        subject: str,
        description: str = "",
        active_form: Optional[str] = None,
        owner: Optional[str] = None,
        blocked_by: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Create a new task.
        
        Args:
            subject: Short title
            description: Detailed description
            active_form: Continuous verb form (e.g., "Running tests")
            owner: Agent ID to assign ownership
            blocked_by: List of task IDs this task depends on
            metadata: Arbitrary metadata
            
        Returns:
            Created task with assigned ID
        """
        task = Task(
            id="0",  # Will be assigned by store
            subject=subject,
            description=description,
            active_form=active_form,
            owner=owner,
            blocked_by=blocked_by or [],
            metadata=metadata or {},
        )
        
        # If blocked, validate that blocker tasks exist
        if task.blocked_by:
            for blocker_id in task.blocked_by:
                blocker = self.store.get(blocker_id)
                if not blocker:
                    raise ValueError(f"Blocker task #{blocker_id} not found")
        
        # If owner specified, mark as in_progress
        if owner:
            task.status = TaskStatus.IN_PROGRESS
        
        return self.store.create(task)
    
    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        check_agent_busy: bool = False,
    ) -> Task:
        """
        Claim a task for an agent.
        
        Validation:
        - Task exists
        - Task is not already claimed
        - Task is not completed
        - Task is not blocked
        - Agent is not busy (if check_agent_busy=True)
        
        Args:
            task_id: Task to claim
            agent_id: Agent claiming the task
            check_agent_busy: Check if agent owns other active tasks
            
        Returns:
            Updated task
            
        Raises:
            ValueError: If claim validation fails
        """
        task = self.store.get(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found")
        
        if task.owner and task.owner != agent_id:
            raise ValueError(f"Task #{task_id} already claimed by {task.owner}")
        
        if task.is_completed:
            raise ValueError(f"Task #{task_id} is already completed")
        
        if task.is_failed:
            raise ValueError(f"Task #{task_id} has failed")
        
        if task.blocked_by:
            raise ValueError(
                f"Task #{task_id} is blocked by: {', '.join(task.blocked_by)}"
            )
        
        if check_agent_busy and self.is_agent_busy(agent_id, exclude_task_id=task_id):
            raise ValueError(
                f"Agent {agent_id} is busy with another task"
            )
        
        # Claim the task
        task.owner = agent_id
        task.status = TaskStatus.IN_PROGRESS
        return self.store.update(task)
    
    def complete_task(self, task_id: str) -> Task:
        """
        Mark a task as completed.
        
        Cascades:
        - Removes this task from blocked_by arrays of dependent tasks
        - Unblocks any tasks that are now free
        
        Args:
            task_id: Task to complete
            
        Returns:
            Updated task
        """
        task = self.store.get(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found")
        
        if task.is_completed:
            return task
        
        # Complete the task
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now()
        task.updated_at = datetime.now()
        self.store.update(task)
        
        # Cascade: unblock dependent tasks
        self._unblock_dependents(task_id)
        
        return task
    
    def fail_task(self, task_id: str, reason: str = "") -> Task:
        """
        Mark a task as failed.
        
        Args:
            task_id: Task to fail
            reason: Optional failure reason
            
        Returns:
            Updated task
        """
        task = self.store.get(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found")
        
        task.status = TaskStatus.FAILED
        task.updated_at = datetime.now()
        if reason:
            task.metadata["failure_reason"] = reason
        
        return self.store.update(task)
    
    def update_task(
        self,
        task_id: str,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        active_form: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Update task fields.
        
        Args:
            task_id: Task to update
            subject: New subject (optional)
            description: New description (optional)
            active_form: New active form (optional)
            status: New status string (optional)
            metadata: Metadata to merge (optional)
            
        Returns:
            Updated task
        """
        task = self.store.get(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} not found")
        
        if subject is not None:
            task.subject = subject
        if description is not None:
            task.description = description
        if active_form is not None:
            task.active_form = active_form
        if status is not None:
            task.status = TaskStatus(status)
            if task.status == TaskStatus.COMPLETED:
                task.completed_at = datetime.now()
        if metadata:
            task.metadata.update(metadata)
        
        return self.store.update(task)
    
    def delete_task(self, task_id: str) -> bool:
        """
        Delete a task.
        
        Cascades reference cleanup (handled by store).
        
        Args:
            task_id: Task to delete
            
        Returns:
            True if deleted
        """
        return self.store.delete(task_id)
    
    def get_next_task(self, agent_id: Optional[str] = None) -> Optional[Task]:
        """
        Get the next available task.
        
        Returns the first task that is:
        - Pending
        - Not claimed by anyone (or claimed by this agent)
        - Not blocked
        
        Args:
            agent_id: Optional agent ID to prefer tasks already claimed by this agent
            
        Returns:
            Next available task or None
        """
        available = self.store.list_available()
        
        if not available:
            return None
        
        # Prefer tasks already claimed by this agent
        if agent_id:
            agent_tasks = [t for t in available if t.owner == agent_id]
            if agent_tasks:
                return agent_tasks[0]
        
        return available[0]
    
    def is_agent_busy(self, agent_id: str, exclude_task_id: Optional[str] = None) -> bool:
        """
        Check if an agent is busy (owns active tasks).
        
        Args:
            agent_id: Agent to check
            exclude_task_id: Task ID to exclude from check
            
        Returns:
            True if agent owns any in-progress tasks
        """
        all_tasks = self.store.list_all()
        for task in all_tasks:
            if task.owner == agent_id and task.is_in_progress:
                if exclude_task_id and task.id == exclude_task_id:
                    continue
                return True
        return False
    
    def get_agent_tasks(self, agent_id: str) -> List[Task]:
        """Get all tasks owned by an agent."""
        return [
            t for t in self.store.list_all()
            if t.owner == agent_id
        ]
    
    def get_task_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all tasks.
        
        Returns:
            Dictionary with counts and status breakdown
        """
        all_tasks = self.store.list_all()
        return {
            "total": len(all_tasks),
            "pending": sum(1 for t in all_tasks if t.is_pending),
            "in_progress": sum(1 for t in all_tasks if t.is_in_progress),
            "completed": sum(1 for t in all_tasks if t.is_completed),
            "failed": sum(1 for t in all_tasks if t.is_failed),
            "blocked": sum(1 for t in all_tasks if t.is_blocked),
        }
    
    def _unblock_dependents(self, completed_task_id: str) -> None:
        """
        Remove completed task from blocked_by arrays.
        
        This allows dependent tasks to become available.
        """
        all_tasks = self.store.list_all()
        for task in all_tasks:
            if completed_task_id in task.blocked_by:
                task.blocked_by.remove(completed_task_id)
                task.updated_at = datetime.now()
                self.store.update(task)
