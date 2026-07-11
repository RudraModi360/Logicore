"""
SessionManifest: Typed dataclass for session snapshot structure.

This is the single manifest stored as session.json in the snapshot tier.
It contains everything about a session: messages, telemetry, metadata,
and attachment references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class AttachmentRef:
    """Reference to a stored attachment (metadata only, not bytes)."""
    file_id: str = ""
    path: str = ""
    mime: str = ""
    sha256: str = ""
    size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "path": self.path,
            "mime": self.mime,
            "sha256": self.sha256,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttachmentRef":
        return cls(
            file_id=data.get("file_id", ""),
            path=data.get("path", ""),
            mime=data.get("mime", ""),
            sha256=data.get("sha256", ""),
            size=data.get("size", 0),
        )


@dataclass
class SessionManifest:
    """
    Typed snapshot manifest for a session.

    This is what gets serialized to session.json in the snapshot tier.
    Single manifest — no separate attachments.json.
    """
    session_id: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    description: str = ""
    revision: int = 1
    context: str = ""
    has_attachments: bool = False
    attachments: List[AttachmentRef] = field(default_factory=list)

    # Telemetry snapshot
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    api_calls: int = 0

    # Timestamps
    created_at: str = ""
    updated_at: str = ""
    synced_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "provider": self.provider,
            "model": self.model,
            "description": self.description,
            "revision": self.revision,
            "context": self.context,
            "has_attachments": self.has_attachments,
            "attachments": [a.to_dict() for a in self.attachments],
            "telemetry": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "tool_calls": self.tool_calls,
                "api_calls": self.api_calls,
                "total_tokens": (
                    self.input_tokens + self.output_tokens
                    + self.cache_read_tokens + self.cache_write_tokens
                    + self.reasoning_tokens
                ),
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionManifest":
        """Deserialize from dictionary."""
        telemetry = data.get("telemetry", {})
        attachments = [
            AttachmentRef.from_dict(a) for a in data.get("attachments", [])
        ]
        return cls(
            session_id=data.get("session_id", ""),
            messages=data.get("messages", []),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            description=data.get("description", ""),
            revision=data.get("revision", 1),
            context=data.get("context", ""),
            has_attachments=data.get("has_attachments", False),
            attachments=attachments,
            input_tokens=telemetry.get("input_tokens", 0),
            output_tokens=telemetry.get("output_tokens", 0),
            cache_read_tokens=telemetry.get("cache_read_tokens", 0),
            cache_write_tokens=telemetry.get("cache_write_tokens", 0),
            reasoning_tokens=telemetry.get("reasoning_tokens", 0),
            tool_calls=telemetry.get("tool_calls", 0),
            api_calls=telemetry.get("api_calls", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            synced_at=data.get("synced_at", ""),
        )
