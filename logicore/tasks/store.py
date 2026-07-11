"""
File-per-task storage with locking infrastructure.

Storage layout (under logicore.config settings.paths.tasks_dir):
    {tasks_dir}/{task_list_id}/
        .lock              # File-lock for list-level operations
        .highwatermark     # Max task ID ever assigned
        1.json             # Task #1
        2.json             # Task #2
        ...

Design:
- Each task is a separate JSON file for parallel reads
- File-level locking for concurrency control
- High water mark prevents ID reuse after deletion
- Abstract lock interface for future multi-agent support

For single-agent mode: No-op locks (same process, no contention)
For multi-agent mode: File-based locks via proper-lockfile equivalent
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from abc import ABC, abstractmethod
import threading

from logicore.tasks.models import Task


# === Lock Interface ===

class TaskLock(ABC):
    """Abstract lock interface for task store concurrency control."""
    
    @abstractmethod
    def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire the lock. Returns True if acquired."""
        pass
    
    @abstractmethod
    def release(self) -> None:
        """Release the lock."""
        pass
    
    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class NoOpLock(TaskLock):
    """No-op lock for single-agent mode (same process, no contention)."""
    
    def acquire(self, timeout: float = 30.0) -> bool:
        return True
    
    def release(self) -> None:
        pass


class ThreadLock(TaskLock):
    """Thread-based lock for in-process concurrency."""
    
    def __init__(self):
        self._lock = threading.Lock()
    
    def acquire(self, timeout: float = 30.0) -> bool:
        return self._lock.acquire(timeout=timeout)
    
    def release(self) -> None:
        self._lock.release()


class FileLock(TaskLock):
    """
    File-based lock for multi-process/multi-agent concurrency.
    
    Uses a .lock file with atomic creation for cross-process locking.
    Falls back to thread lock if file locking fails.
    
    For production multi-agent, replace with proper-lockfile equivalent.
    """
    
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._thread_lock = threading.Lock()
        self._acquired = False
    
    def acquire(self, timeout: float = 30.0) -> bool:
        import time
        start_time = time.time()
        retry_count = 0
        max_retries = 30
        min_timeout = 0.005  # 5ms
        max_timeout = 0.1    # 100ms
        
        while retry_count < max_retries:
            try:
                # Atomic creation of lock file
                fd = os.open(
                    str(self._lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644
                )
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self._acquired = True
                return True
            except FileExistsError:
                # Lock file exists, check if stale
                try:
                    lock_age = time.time() - os.path.getmtime(str(self._lock_path))
                    if lock_age > 60:  # Stale lock after 60s
                        os.remove(str(self._lock_path))
                        continue
                except OSError:
                    pass
                
                # Exponential backoff
                import random
                delay = min_timeout * (2 ** retry_count)
                delay = min(delay, max_timeout)
                delay = random.uniform(0, delay)
                time.sleep(delay)
                retry_count += 1
        
        return False
    
    def release(self) -> None:
        if self._acquired:
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._acquired = False


# === Task Store ===

class TaskStore:
    """
    File-per-task storage with locking infrastructure.
    
    Storage layout (under logicore.config settings.paths.tasks_dir):
        {tasks_dir}/{task_list_id}/
            .lock              # List-level lock
            .highwatermark     # Max task ID ever assigned
            1.json             # Task #1
            2.json             # Task #2
    
    Features:
- Each task is a separate JSON file for parallel reads
- High water mark prevents ID reuse after deletion
- List-level lock for task creation (serializes ID generation)
- Task-level operations for concurrent access to different tasks
    """
    
    def __init__(
        self,
        base_dir: str,
        task_list_id: str = "default",
        use_file_locks: bool = False,
    ):
        """
        Initialize task store.
        
        Args:
            base_dir: Base directory for task storage
            task_list_id: Identifier for this task list (shared across agents)
            use_file_locks: Enable file-based locks (for multi-agent)
        """
        self.base_dir = Path(base_dir)
        self.task_list_id = task_list_id
        self.tasks_dir = self.base_dir / task_list_id
        self.lock_file = self.tasks_dir / ".lock"
        self.highwatermark_file = self.tasks_dir / ".highwatermark"
        
        # Create directory
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize locks
        if use_file_locks:
            self._list_lock = FileLock(self.lock_file)
        else:
            self._list_lock = ThreadLock()
        
        # Load high water mark
        self._highwatermark = self._load_highwatermark()
    
    def _load_highwatermark(self) -> int:
        """Load the high water mark from disk."""
        try:
            if self.highwatermark_file.exists():
                return int(self.highwatermark_file.read_text().strip())
        except (ValueError, OSError):
            pass
        return 0
    
    def _save_highwatermark(self, value: int) -> None:
        """Save the high water mark to disk."""
        try:
            self.highwatermark_file.write_text(str(value))
        except OSError:
            pass
    
    def _get_next_id(self) -> str:
        """
        Get the next task ID.
        
        Scans existing files AND checks high water mark to determine
        the next ID. This prevents ID reuse after deletion.
        """
        # Find max ID from existing files
        max_id = self._highwatermark
        
        for item in self.tasks_dir.iterdir():
            if item.suffix == ".json":
                try:
                    task_id = int(item.stem)
                    max_id = max(max_id, task_id)
                except ValueError:
                    continue
        
        next_id = max_id + 1
        
        # Update high water mark
        self._highwatermark = next_id
        self._save_highwatermark(next_id)
        
        return str(next_id)
    
    def _task_path(self, task_id: str) -> Path:
        """Get the file path for a task."""
        return self.tasks_dir / f"{task_id}.json"
    
    def create(self, task: Task) -> Task:
        """
        Create a new task.
        
        Acquires list-level lock to serialize ID generation.
        """
        with self._list_lock:
            # Assign ID if not set
            if not task.id or task.id == "0":
                task.id = self._get_next_id()
            
            # Save task
            self._save_task(task)
            return task
    
    def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        task_path = self._task_path(task_id)
        if not task_path.exists():
            return None
        
        try:
            data = json.loads(task_path.read_text(encoding="utf-8"))
            return Task.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
    
    def update(self, task: Task) -> Task:
        """Update an existing task."""
        task.updated_at = datetime.now()
        self._save_task(task)
        return task
    
    def delete(self, task_id: str) -> bool:
        """
        Delete a task and remove references from other tasks.
        
        Updates high water mark and cascades reference cleanup.
        """
        with self._list_lock:
            task_path = self._task_path(task_id)
            if not task_path.exists():
                return False
            
            # Delete the task file
            task_path.unlink(missing_ok=True)
            
            # Remove references from other tasks
            for item in self.tasks_dir.iterdir():
                if item.suffix == ".json" and item.stem != task_id:
                    try:
                        data = json.loads(item.read_text(encoding="utf-8"))
                        modified = False
                        
                        # Remove from blocks arrays
                        if task_id in data.get("blocks", []):
                            data["blocks"].remove(task_id)
                            modified = True
                        
                        # Remove from blocked_by arrays
                        if task_id in data.get("blocked_by", []):
                            data["blocked_by"].remove(task_id)
                            modified = True
                        
                        if modified:
                            item.write_text(
                                json.dumps(data, indent=2, default=str),
                                encoding="utf-8"
                            )
                    except (json.JSONDecodeError, OSError):
                        continue
            
            return True
    
    def list_all(self) -> List[Task]:
        """List all tasks in the task list."""
        tasks = []
        for item in sorted(self.tasks_dir.iterdir()):
            if item.suffix == ".json":
                try:
                    data = json.loads(item.read_text(encoding="utf-8"))
                    tasks.append(Task.from_dict(data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return tasks
    
    def list_by_status(self, status: str) -> List[Task]:
        """List tasks by status."""
        return [t for t in self.list_all() if t.status.value == status]
    
    def list_available(self) -> List[Task]:
        """
        List tasks available for claiming.
        
        Available = pending + no owner + not blocked
        """
        return [t for t in self.list_all() if t.is_available]
    
    def list_blocked(self) -> List[Task]:
        """List tasks that are blocked by dependencies."""
        return [t for t in self.list_all() if t.is_blocked]
    
    def get_dependency_chain(self, task_id: str) -> List[str]:
        """
        Get the chain of task IDs that block this task (recursive).
        
        Returns ordered list from closest blocker to root.
        """
        visited = set()
        chain = []
        
        def _traverse(tid: str):
            if tid in visited:
                return
            visited.add(tid)
            
            task = self.get(tid)
            if not task:
                return
            
            for blocker_id in task.blocked_by:
                _traverse(blocker_id)
                if blocker_id not in chain:
                    chain.append(blocker_id)
        
        _traverse(task_id)
        return chain
    
    def _save_task(self, task: Task) -> None:
        """Save task to file."""
        task_path = self._task_path(task.id)
        data = task.to_dict()
        task_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8"
        )
    
    def reset(self) -> None:
        """
        Delete all tasks and reset the task list.
        
        Preserves high water mark for ID monotonicity.
        """
        with self._list_lock:
            for item in self.tasks_dir.iterdir():
                if item.suffix == ".json":
                    item.unlink(missing_ok=True)
