"""
MessagePipeline: Message injection and removal utilities.

Handles injecting system hints (like reminder routing) into message history
and removing them after the conversation turn completes.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional


class MessagePipeline:
    """
    Utilities for manipulating message history.

    Provides clean methods for injecting and removing system messages
    without polluting the main agent loop.
    """

    @staticmethod
    def inject_system_message(
        messages: List[Dict[str, Any]],
        content: str,
        position: int = -1,
    ) -> int:
        """
        Inject a system message into the message list.

        Args:
            messages: Message list (mutated in place)
            content: System message content
            position: Index to insert at (-1 = before last message)

        Returns:
            Index where the message was inserted
        """
        insert_at = position if position >= 0 else len(messages) + position
        insert_at = max(0, min(insert_at, len(messages)))

        messages.insert(insert_at, {
            "role": "system",
            "content": content,
        })
        return insert_at

    @staticmethod
    def remove_system_message(
        messages: List[Dict[str, Any]],
        content: str,
    ) -> bool:
        """
        Remove the first system message matching exact content.

        Returns True if a message was removed.
        """
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") == "system" and msg.get("content") == content:
                del messages[idx]
                return True
        return False

    @staticmethod
    def get_system_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Get all system messages from the list."""
        return [m for m in messages if m.get("role") == "system"]

    @staticmethod
    def get_non_system_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Get all non-system messages from the list."""
        return [m for m in messages if m.get("role") != "system"]

    @staticmethod
    def estimate_message_chars(messages: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Estimate character counts by category.

        Returns dict with keys: system, messages, tools, total.
        """
        counts = {"system": 0, "messages": 0, "tools": 0}
        for msg in messages:
            role = msg.get("role", "")
            content_len = len(str(msg.get("content", "")))
            if role == "system":
                counts["system"] += content_len
            elif role == "tool":
                counts["tools"] += content_len
            else:
                counts["messages"] += content_len
        counts["total"] = sum(counts.values())
        return counts
