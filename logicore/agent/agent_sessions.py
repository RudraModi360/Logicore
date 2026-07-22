"""
AgentSessionsMixin: Session management extracted from Agent.

Consolidates all session-related functionality: creation, retrieval,
deletion, storage persistence, and VFS file handling.

Agent inherits from this mixin to maintain the same public API.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from logicore.agent.session import AgentSession

if TYPE_CHECKING:
    import asyncio

logger = logging.getLogger(__name__)

# MIME mapping for VFS files
_MIME_MAP = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "pdf": "application/pdf", "txt": "text/plain", "csv": "text/csv",
    "json": "application/json", "md": "text/markdown", "html": "text/html",
    "htm": "text/html", "css": "text/css", "js": "text/javascript",
    "zip": "application/zip",
}


class AgentSessionsMixin:
    """Mixin providing session management for Agent.

    Expects the following attributes to be set on the host class:
    - sessions: Dict[str, AgentSession]
    - _session_locks: Dict[str, asyncio.Lock]
    - _storage: Optional[StorageBackend]
    - default_system_message: str
    - tool_executor: ToolExecutor
    - debug: bool
    - _task_store: Optional[TaskStore]
    - _task_manager: Optional[TaskManager]
    - _session_progress_writers: Dict[str, SessionProgressWriter]
    """

    def _get_session_lock(self, session_id: str) -> "asyncio.Lock":
        if session_id not in self._session_locks:
            import asyncio
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    def get_session(self, session_id: str = "default") -> AgentSession:
        if session_id not in self.sessions:
            restored = self._restore_session_from_storage(session_id)
            if restored is None:
                restored = AgentSession(session_id, self.default_system_message)
            self.sessions[session_id] = restored
        return self.sessions[session_id]

    def _restore_session_from_storage(self, session_id: str):
        """Load a previous session from storage and reconstruct in-memory state.

        Returns ``None`` when no prior data exists or storage is not configured.
        """
        if not self._storage or not self._storage.initialized:
            return None
        try:
            stored = self._storage.load_session(session_id)
            if not stored:
                return None
            logger.debug(
                "[Agent] restoring session %s from storage | messages=%d",
                session_id, len(stored),
            )
            session = AgentSession(session_id, self.default_system_message)
            history = []
            for m in stored:
                if m.get("role") == "system":
                    content = m.get("content", "")
                    if "Previous Conversation Summary" in content:
                        history.append(m)
                else:
                    history.append(m)
            session.messages = [{"role": "system", "content": self.default_system_message}] + history
            saved_meta = self._storage.load_session_metadata(session_id)
            if saved_meta:
                refs = saved_meta.pop("_vfs_files", None)
                if refs:
                    for ref in refs:
                        try:
                            data = self._storage.load_attachment(ref["path"])
                            if data is None:
                                continue
                            content = data.decode("utf-8") if ref.get("mime", "").startswith("text/") or _is_text(data) else data.decode("utf-8", "replace")
                            session.files[ref["name"]] = content
                        except Exception as fe:
                            if self.debug:
                                logger.warning("[Agent] failed to restore file %s: %s", ref.get("name"), fe)
                session.metadata.update(saved_meta)
            return session
        except Exception as e:
            if self.debug:
                logger.warning("[Agent] failed to restore session %s: %s", session_id, e)
            return None

    def clear_session(self, session_id: str = "default"):
        if session_id in self.sessions:
            self.sessions[session_id].clear_history()
        self.tool_executor.clear_session_approvals(session_id)

    def create_session(self, session_id: str = None, tags: Dict[str, str] = None) -> str:
        if session_id is None:
            import uuid
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        session = AgentSession(session_id, self.default_system_message)
        if tags:
            session.metadata["tags"] = tags
        session.metadata["created_at"] = datetime.now().isoformat()
        self.sessions[session_id] = session
        return session_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        result = []
        for session_id, session in self.sessions.items():
            result.append({
                "session_id": session_id,
                "tags": session.metadata.get("tags", {}),
                "message_count": len(session.messages),
                "created_at": session.created_at.isoformat() if hasattr(session, 'created_at') else None,
                "last_activity": session.last_activity.isoformat() if hasattr(session, 'last_activity') else None,
            })
        return result

    def delete_session(self, session_id: str) -> bool:
        if session_id not in self.sessions:
            return False
        del self.sessions[session_id]
        self.tool_executor.clear_session_approvals(session_id)
        if self._task_store and self._task_store.task_list_id == session_id:
            self._task_manager = None
            self._task_store = None
        if session_id in self._session_progress_writers:
            del self._session_progress_writers[session_id]
        return True

    def get_session_by_tags(self, tags: Dict[str, str]) -> Optional[str]:
        for session_id, session in self.sessions.items():
            session_tags = session.metadata.get("tags", {})
            if all(session_tags.get(k) == v for k, v in tags.items()):
                return session_id
        return None


def _is_text(data: bytes) -> bool:
    """Heuristic: treat content as text if it decodes cleanly as UTF-8."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def safe_file_id(name: str) -> str:
    """Turn a VFS filename into a filesystem-safe, unique-per-session id."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not base:
        base = "file"
    return base


def guess_mime(name: str) -> str:
    """Best-effort MIME guess from a filename extension."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MIME_MAP.get(ext, "application/octet-stream")
