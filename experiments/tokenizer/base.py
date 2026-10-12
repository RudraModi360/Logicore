"""Contracts for the tokenizer strategy sub-package (experiments prototype).

Implements the TOKEN_SYSTEM_SPEC Component A base types:
- TokenCountResult: what a count produced + how honest it is
- TokenizerStrategy: the protocol every provider strategy implements
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

CountMethod = Literal["native_api", "local_tokenizer", "runtime_usage", "heuristic", "unknown"]
Exactness = Literal["provider_reported", "model_tokenizer", "estimated", "unknown"]
AccuracyClass = Literal["exact", "high", "medium", "estimated"]


@dataclass
class TokenCountResult:
    """A token count plus the provenance needed to trust (or distrust) it.

    ``input_tokens=None`` is valid and means "cannot determine" — callers
    must treat that as unknown, never as zero.
    """

    input_tokens: Optional[int]
    method: CountMethod = "unknown"
    exactness: Exactness = "unknown"
    model: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "method": self.method,
            "exactness": self.exactness,
            "model": self.model,
            "warnings": list(self.warnings),
        }


class TokenizerStrategy(ABC):
    """Provider-aware token counting strategy.

    Implementations are stateless after construction; tokenizers are loaded
    lazily on first count and cached on the instance.
    """

    provider: str = "unknown"

    @property
    @abstractmethod
    def accuracy_class(self) -> AccuracyClass:
        """How much this strategy's numbers should be trusted."""

    @abstractmethod
    def count(self, text: str) -> int:
        """Count tokens for a raw string (no role overhead)."""

    def count_message(self, msg: Dict[str, Any]) -> int:
        """Count a single chat message including per-provider role overhead."""
        return self.count(_message_text(msg))

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        """Count a full conversation, including role overhead per message."""
        total = 0
        for m in messages or []:
            total += self.count_message(m)
        return total

    def count_tools(self, tool_defs: List[Dict[str, Any]]) -> int:
        """Count tool schema tokens as compact JSON (schema tokens are real tokens)."""
        if not tool_defs:
            return 0
        blob = json.dumps(tool_defs, separators=(",", ":"), ensure_ascii=False)
        return self.count(blob)

    def describe(self) -> Dict[str, Any]:
        """Human-readable identity for reports and the session ledger."""
        return {
            "provider": self.provider,
            "strategy": type(self).__name__,
            "accuracy_class": self.accuracy_class,
        }


def _message_text(msg: Dict[str, Any]) -> str:
    """Flatten a chat message's content (str or list-of-parts) to plain text."""
    content = msg.get("content", "")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    return str(content)
