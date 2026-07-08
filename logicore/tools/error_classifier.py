"""Structured tool-execution error classification for Logicore.

Mirrors the design of the hermes-agent parent's ``agent/error_classifier.py``,
but scoped to **tool execution** rather than LLM API calls. The parent gives
the LLM-call path a structured ``FailoverReason`` / ``ClassifiedError``
taxonomy with ``retryable`` / ``should_rotate_credential`` hints so the retry
loop can recover autonomously. Tool execution currently returns an opaque
``{"error": "<str>"}`` — there is no way for the agent loop to know whether a
failure is a transient network blip (retry), an expired OAuth/MCP credential
(rotate + retry), or a deterministic bad-input error (don't retry).

This module supplies that same structured taxonomy for the tool path so the
agent can:

* retry transient tool failures automatically (autonomy, minimal user nudging),
* trigger credential rotation for MCP/OAuth-backed tools (parity with the
  LLM-call failover path),
* avoid useless retries on deterministic input/format errors.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("logicore.tools.error_classifier")


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class ToolFailoverReason(enum.Enum):
    """Why a tool execution failed — determines the recovery strategy."""

    # Transient transport / infra
    timeout = "timeout"                  # Tool call timed out / connection dropped
    overloaded = "overloaded"            # Upstream tool backend 503/529
    server_error = "server_error"        # 5xx from a tool backend (MCP/HTTP)

    # Auth / credentials
    auth = "auth"                        # Expired/invalid credential — rotate + retry
    auth_permanent = "auth_permanent"    # Auth failed even after rotation — abort

    # Rate limiting
    rate_limit = "rate_limit"            # 429 / throttled — backoff then retry

    # Input / request
    validation = "validation"            # Bad args / schema mismatch — don't retry
    not_found = "not_found"              # Tool/resource missing — don't retry
    permission = "permission"            # Tool blocked by policy/permissions

    # Unknown — treat as retryable with backoff
    unknown = "unknown"


# Patterns grouped by category. Kept narrow to avoid false positives that
# would misroute a deterministic failure into a retry loop.
_TIMEOUT_PATTERNS = [
    "timed out", "timeout", "deadline exceeded", "operation timed out",
    "upstream timed out", "connection reset", "connection aborted",
    "remote disconnected", "server disconnected",
]
_OVERLOADED_PATTERNS = ["503", "529", "service unavailable", "overloaded", "unavailable"]
_SERVER_ERROR_PATTERNS = [
    "500", "502", "internal server error", "bad gateway", "gateway error",
]
_AUTH_PATTERNS = [
    "401", "403", "unauthorized", "forbidden", "invalid api key", "invalid_api_key",
    "invalid token", "token expired", "token revoked", "authentication failed",
    "auth failed", "credential", "api key", "oauth", "expired credential",
]
_RATE_LIMIT_PATTERNS = [
    "429", "rate limit", "rate_limit", "too many requests", "throttled",
    "quota exceeded", "resource_exhausted",
]
_VALIDATION_PATTERNS = [
    "validation error", "invalid argument", "schema", "required field",
    "missing required", "type error", "value error", "could not parse",
]
_NOT_FOUND_PATTERNS = [
    "not found", "does not exist", "no such", "unknown tool", "missing",
]
_PERMISSION_PATTERNS = [
    "permission denied", "access denied", "not allowed", "forbidden by policy",
]


@dataclass
class ClassifiedToolError:
    """Structured classification of a tool-execution error."""

    reason: ToolFailoverReason
    message: str = ""
    tool_name: str = ""
    # Recovery hints — the caller consults these instead of re-parsing strings.
    retryable: bool = True
    should_rotate_credential: bool = False
    should_backoff: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "error_category": self.reason.value,
            "retryable": self.retryable,
            "should_rotate_credential": self.should_rotate_credential,
            "should_backoff": self.should_backoff,
        }


# ---------------------------------------------------------------------------
# Classification pipeline
# ---------------------------------------------------------------------------


def _msg(error: BaseException) -> str:
    raw = f"{type(error).__name__}: {error}"
    # Pull a message out of a wrapped dict/tool result if present.
    inner = getattr(error, "args", None)
    if inner and isinstance(inner, tuple) and inner:
        first = inner[0]
        if isinstance(first, str) and first not in raw:
            raw = f"{raw} {first}"
    return raw.lower()


def classify_tool_error(
    error: BaseException,
    *,
    tool_name: str = "",
    is_credentialed: bool = False,
) -> ClassifiedToolError:
    """Classify a tool-execution exception into a recovery recommendation.

    Args:
        error: The exception raised while executing the tool.
        tool_name: The tool that failed (used for logging/diagnostics).
        is_credentialed: Whether the tool is backed by external credentials
            (MCP/OAuth). When True, auth-classified failures get
            ``should_rotate_credential=True`` so the agent loop can rotate
            and retry — mirroring the LLM-call failover path.
    """
    msg = _msg(error)

    # Order matters: most-specific / most-actionable first.
    if any(p in msg for p in _RATE_LIMIT_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.rate_limit,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_rotate_credential=is_credentialed,
            should_backoff=True,
        )

    if any(p in msg for p in _AUTH_PATTERNS):
        # If not credentialed, this is a deterministic auth failure, not a
        # rotatable one (e.g. a tool hard-requires a missing API key).
        return ClassifiedToolError(
            reason=ToolFailoverReason.auth,
            message=str(error),
            tool_name=tool_name,
            retryable=is_credentialed,
            should_rotate_credential=is_credentialed,
            should_backoff=False,
        )

    if any(p in msg for p in _OVERLOADED_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.overloaded,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
        )

    if any(p in msg for p in _SERVER_ERROR_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.server_error,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
        )

    if any(p in msg for p in _VALIDATION_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.validation,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
        )

    if any(p in msg for p in _NOT_FOUND_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.not_found,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
        )

    if any(p in msg for p in _PERMISSION_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.permission,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
        )

    if any(p in msg for p in _TIMEOUT_PATTERNS) or isinstance(
        error, (TimeoutError, ConnectionError, OSError)
    ):
        return ClassifiedToolError(
            reason=ToolFailoverReason.timeout,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
        )

    # Catch-all: retryable with backoff (safe default — a deterministic error
    # that looks unknown will simply fail again and surface to the user).
    return ClassifiedToolError(
        reason=ToolFailoverReason.unknown,
        message=str(error),
        tool_name=tool_name,
        retryable=True,
    )


__all__ = [
    "ToolFailoverReason",
    "ClassifiedToolError",
    "classify_tool_error",
]
