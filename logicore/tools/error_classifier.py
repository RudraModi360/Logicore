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

The classifier is **side-effect-free** — it only decides, never executes.
The conversation loop owns execution and enforcement.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("logicore.tools.error_classifier")


# ---------------------------------------------------------------------------
# Taxonomy — Error Reasons
# ---------------------------------------------------------------------------


class ToolFailoverReason(enum.Enum):
    """Why a tool execution failed — determines the recovery strategy.

    Organized by category with most-specific / most-actionable first.
    Each reason maps to a specific ``RecoveryAction`` in the classification
    pipeline.
    """

    # --- Transient transport / infra ---
    timeout = "timeout"                  # Tool call timed out / connection dropped
    overloaded = "overloaded"            # Upstream tool backend 503/529
    server_error = "server_error"        # 5xx from a tool backend (MCP/HTTP)

    # --- Auth / credentials ---
    auth = "auth"                        # Expired/invalid credential — rotate + retry
    auth_permanent = "auth_permanent"    # Auth failed even after rotation — abort

    # --- Rate limiting ---
    rate_limit = "rate_limit"            # 429 / throttled — backoff then retry

    # --- Context / payload ---
    context_overflow = "context_overflow"    # Prompt/context too large for model
    payload_too_large = "payload_too_large"  # Request body exceeds size limit (413)
    image_too_large = "image_too_large"      # Image exceeds size limit

    # --- Content / policy ---
    content_policy_blocked = "content_policy_blocked"  # Content filtered by provider
    model_not_found = "model_not_found"                # Requested model doesn't exist

    # --- Input / request ---
    format_error = "format_error"        # Malformed request / invalid JSON / bad schema
    validation = "validation"            # Bad args / schema mismatch — don't retry
    not_found = "not_found"              # Tool/resource missing — don't retry
    permission = "permission"            # Tool blocked by policy/permissions

    # --- Unknown — treat as retryable with backoff ---
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Recovery Actions — What to do about it
# ---------------------------------------------------------------------------


class RecoveryAction(enum.Enum):
    """Specific recovery strategy for each error type.

    The classifier returns one of these so the caller can take the right
    action without re-parsing error strings.
    """

    RETRY_SAME = "retry_same"              # Transient: same call is fine
    RETRY_DIFFERENT = "retry_different"    # Strategy change needed
    ROTATE_CREDENTIAL = "rotate_credential"  # Auth: rotate + retry
    COMPRESS_CONTEXT = "compress_context"  # Context/payload too large
    BACKOFF_AND_RETRY = "backoff_and_retry"  # Rate limit / overloaded
    INJECT_SIGNAL = "inject_signal"        # Tell LLM to change approach
    ABORT_WITH_MESSAGE = "abort_with_message"  # Unrecoverable — stop


# Mapping: reason -> recovery action (with overrides for specific cases)
_DEFAULT_RECOVERY: Dict[ToolFailoverReason, RecoveryAction] = {
    ToolFailoverReason.timeout: RecoveryAction.RETRY_SAME,
    ToolFailoverReason.overloaded: RecoveryAction.BACKOFF_AND_RETRY,
    ToolFailoverReason.server_error: RecoveryAction.BACKOFF_AND_RETRY,
    ToolFailoverReason.auth: RecoveryAction.ROTATE_CREDENTIAL,
    ToolFailoverReason.auth_permanent: RecoveryAction.ABORT_WITH_MESSAGE,
    ToolFailoverReason.rate_limit: RecoveryAction.BACKOFF_AND_RETRY,
    ToolFailoverReason.context_overflow: RecoveryAction.COMPRESS_CONTEXT,
    ToolFailoverReason.payload_too_large: RecoveryAction.COMPRESS_CONTEXT,
    ToolFailoverReason.image_too_large: RecoveryAction.RETRY_DIFFERENT,
    ToolFailoverReason.content_policy_blocked: RecoveryAction.ABORT_WITH_MESSAGE,
    ToolFailoverReason.model_not_found: RecoveryAction.ABORT_WITH_MESSAGE,
    ToolFailoverReason.format_error: RecoveryAction.RETRY_DIFFERENT,
    ToolFailoverReason.validation: RecoveryAction.INJECT_SIGNAL,
    ToolFailoverReason.not_found: RecoveryAction.INJECT_SIGNAL,
    ToolFailoverReason.permission: RecoveryAction.ABORT_WITH_MESSAGE,
    ToolFailoverReason.unknown: RecoveryAction.RETRY_SAME,
}


# ---------------------------------------------------------------------------
# Pattern Lists — Grouped by category
# ---------------------------------------------------------------------------

_TIMEOUT_PATTERNS = (
    "timed out", "timeout", "deadline exceeded", "operation timed out",
    "upstream timed out", "connection reset", "connection aborted",
    "remote disconnected", "server disconnected",
)
_OVERLOADED_PATTERNS = ("503", "529", "service unavailable", "overloaded", "unavailable")
_SERVER_ERROR_PATTERNS = ("500", "502", "internal server error", "bad gateway", "gateway error")
_AUTH_PATTERNS = (
    "401", "403", "unauthorized", "forbidden", "invalid api key", "invalid_api_key",
    "invalid token", "token expired", "token revoked", "authentication failed",
    "auth failed", "credential", "api key", "oauth", "expired credential",
)
_RATE_LIMIT_PATTERNS = (
    "429", "rate limit", "rate_limit", "too many requests", "throttled",
    "quota exceeded", "resource_exhausted",
)
_VALIDATION_PATTERNS = (
    "validation error", "invalid argument", "schema", "required field",
    "missing required", "type error", "value error", "could not parse",
)
_NOT_FOUND_PATTERNS = ("not found", "does not exist", "no such", "unknown tool", "missing")
_PERMISSION_PATTERNS = ("permission denied", "access denied", "not allowed", "forbidden by policy")

# New patterns for expanded error types
_CONTEXT_OVERFLOW_PATTERNS = (
    "context length", "context_length", "context too long", "prompt is too long",
    "maximum context length", "max tokens exceeded", "input is too long",
    "request too large for model", "token limit", "context window exceeded",
    "prompt_too_long",
)
_PAYLOAD_TOO_LARGE_PATTERNS = (
    "413", "payload too large", "request entity too large", "content length",
    "max request size", "body too large",
)
_IMAGE_TOO_LARGE_PATTERNS = (
    "image too large", "image_size", "image exceeds", "image is too big",
    "pixel limit", "resolution too high", "image dimensions",
)
_CONTENT_POLICY_PATTERNS = (
    "content_policy", "content policy", "flagged", "blocked by policy",
    "safety", "harmful", "inappropriate", "violates", "nsfw",
    "content_filter", "content filter",
)
_MODEL_NOT_FOUND_PATTERNS = (
    "model not found", "model_not_found", "unknown model", "no such model",
    "model does not exist", "invalid model",
)
_FORMAT_ERROR_PATTERNS = (
    "invalid json", "json decode", "parse error", "unexpected token",
    "malformed", "invalid format", "bad request", "invalid request",
    "unsupported parameter", "unknown parameter", "unexpected_key",
)


# ---------------------------------------------------------------------------
# Structured Classification Result
# ---------------------------------------------------------------------------


@dataclass
class ClassifiedToolError:
    """Structured classification of a tool-execution error.

    Contains the error reason, a recommended recovery action, and hints
    the caller can use to make autonomous recovery decisions. The classifier
    is side-effect-free — this dataclass carries the decision, the caller
    owns enforcement.
    """

    reason: ToolFailoverReason
    recovery_action: RecoveryAction = RecoveryAction.RETRY_SAME
    message: str = ""
    tool_name: str = ""

    # Recovery hints — the caller consults these instead of re-parsing strings.
    retryable: bool = True
    should_rotate_credential: bool = False
    should_backoff: bool = False

    # Context for the caller (e.g., HTTP status code, provider name)
    status_code: Optional[int] = None
    error_context: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_auth(self) -> bool:
        return self.reason in {ToolFailoverReason.auth, ToolFailoverReason.auth_permanent}

    @property
    def should_compress(self) -> bool:
        return self.recovery_action == RecoveryAction.COMPRESS_CONTEXT

    @property
    def should_inject_signal(self) -> bool:
        return self.recovery_action == RecoveryAction.INJECT_SIGNAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "error_category": self.reason.value,
            "recovery_action": self.recovery_action.value,
            "retryable": self.retryable,
            "should_rotate_credential": self.should_rotate_credential,
            "should_backoff": self.should_backoff,
            "should_compress": self.should_compress,
            "status_code": self.status_code,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(error: BaseException) -> str:
    """Extract a lowercase message string from an exception."""
    raw = f"{type(error).__name__}: {error}"
    inner = getattr(error, "args", None)
    if inner and isinstance(inner, tuple) and inner:
        first = inner[0]
        if isinstance(first, str) and first not in raw:
            raw = f"{raw} {first}"
    return raw.lower()


def _has_pattern(msg: str, patterns: Tuple[str, ...]) -> bool:
    """Check if any pattern appears in the message."""
    return any(p in msg for p in patterns)


def _extract_status_code(error: BaseException) -> Optional[int]:
    """Try to extract an HTTP status code from the error."""
    # Check for status_code attribute (httpx, requests, etc.)
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int) and 100 <= status < 600:
        return status
    # Try to extract from message
    msg = str(error)
    match = re.search(r'\b([45]\d{2})\b', msg)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# 402 / 400 Sub-Classifier
# ---------------------------------------------------------------------------

# 402 disambiguation: billing vs rate_limit
_USAGE_LIMIT_PATTERNS = (
    "usage limit", "quota", "entitlement", "plan limit", "credit",
)
_USAGE_LIMIT_TRANSIENT_PATTERNS = (
    "try again", "later", "minutes", "resets", "temporary",
)

# 400 sub-classification priority order
_400_SUB_CLASSIFIERS: list[Tuple[Tuple[str, ...], ToolFailoverReason, RecoveryAction]] = [
    # Content policy (check first — "bad request" substrings shadow this if format checked first)
    (_CONTENT_POLICY_PATTERNS, ToolFailoverReason.content_policy_blocked, RecoveryAction.ABORT_WITH_MESSAGE),
    # Context overflow
    (_CONTEXT_OVERFLOW_PATTERNS, ToolFailoverReason.context_overflow, RecoveryAction.COMPRESS_CONTEXT),
    # Format errors (non-retryable)
    (_FORMAT_ERROR_PATTERNS, ToolFailoverReason.format_error, RecoveryAction.RETRY_DIFFERENT),
    # Model not found
    (_MODEL_NOT_FOUND_PATTERNS, ToolFailoverReason.model_not_found, RecoveryAction.ABORT_WITH_MESSAGE),
    # Rate limit disguised as 400
    (_RATE_LIMIT_PATTERNS, ToolFailoverReason.rate_limit, RecoveryAction.BACKOFF_AND_RETRY),
]


def _classify_402(msg: str) -> Tuple[ToolFailoverReason, RecoveryAction]:
    """Disambiguate 402: billing vs rate_limit."""
    has_usage_limit = _has_pattern(msg, _USAGE_LIMIT_PATTERNS)
    has_transient = _has_pattern(msg, _USAGE_LIMIT_TRANSIENT_PATTERNS)
    if has_usage_limit and has_transient:
        return ToolFailoverReason.rate_limit, RecoveryAction.BACKOFF_AND_RETRY
    return ToolFailoverReason.auth, RecoveryAction.ABORT_WITH_MESSAGE


def _classify_400(msg: str) -> Tuple[ToolFailoverReason, RecoveryAction]:
    """Sub-classify 400 errors by priority order."""
    for patterns, reason, action in _400_SUB_CLASSIFIERS:
        if _has_pattern(msg, patterns):
            return reason, action
    # Default 400: format error
    return ToolFailoverReason.format_error, RecoveryAction.RETRY_DIFFERENT


# ---------------------------------------------------------------------------
# Main Classification Pipeline
# ---------------------------------------------------------------------------


def classify_tool_error(
    error: BaseException,
    *,
    tool_name: str = "",
    is_credentialed: bool = False,
    approx_tokens: int = 0,
    context_length: int = 200000,
) -> ClassifiedToolError:
    """Classify a tool-execution exception into a recovery recommendation.

    This is a **side-effect-free pure function** — it only decides, never
    executes. The conversation loop owns enforcement.

    The classification follows a priority-ordered pipeline (most-specific /
    most-actionable first):

    1. Status code classification (402, 400 sub-classifiers)
    2. Pattern matching (rate_limit, auth, overloaded, server_error, etc.)
    3. Context/payload checks (context_overflow, payload_too_large)
    4. Transport error heuristics (timeout)
    5. Fallback: unknown (retryable)

    Args:
        error: The exception raised while executing the tool.
        tool_name: The tool that failed (used for logging/diagnostics).
        is_credentialed: Whether the tool is backed by external credentials
            (MCP/OAuth). When True, auth-classified failures get
            ``should_rotate_credential=True`` so the agent loop can rotate
            and retry — mirroring the LLM-call failover path.
        approx_tokens: Approximate token count of the request (for context
            overflow disambiguation).
        context_length: Model's context window size (for context overflow
            disambiguation).
    """
    msg = _msg(error)
    status_code = _extract_status_code(error)

    # --- Step 1: Status-code driven classification ---

    # 402: Disambiguate billing vs rate_limit
    if status_code == 402:
        reason, action = _classify_402(msg)
        return ClassifiedToolError(
            reason=reason,
            recovery_action=action,
            message=str(error),
            tool_name=tool_name,
            retryable=(reason == ToolFailoverReason.rate_limit),
            should_backoff=(reason == ToolFailoverReason.rate_limit),
            status_code=status_code,
        )

    # 400: Sub-classify by pattern priority
    if status_code == 400:
        reason, action = _classify_400(msg)
        retryable = reason not in {
            ToolFailoverReason.format_error,
            ToolFailoverReason.content_policy_blocked,
            ToolFailoverReason.model_not_found,
        }
        return ClassifiedToolError(
            reason=reason,
            recovery_action=action,
            message=str(error),
            tool_name=tool_name,
            retryable=retryable,
            status_code=status_code,
        )

    # 413: Payload too large
    if status_code == 413:
        return ClassifiedToolError(
            reason=ToolFailoverReason.payload_too_large,
            recovery_action=RecoveryAction.COMPRESS_CONTEXT,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            status_code=status_code,
        )

    # --- Step 2: Pattern matching (order matters: most-specific first) ---

    if _has_pattern(msg, _RATE_LIMIT_PATTERNS):
        # Extract retry-after from message if present, else default by status code
        retry_after = 2.0
        if status_code == 429:
            retry_after = 5.0
        return ClassifiedToolError(
            reason=ToolFailoverReason.rate_limit,
            recovery_action=RecoveryAction.BACKOFF_AND_RETRY,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_rotate_credential=is_credentialed,
            should_backoff=True,
            status_code=status_code,
            error_context={"retry_after_seconds": retry_after},
        )

    if _has_pattern(msg, _AUTH_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.auth,
            recovery_action=RecoveryAction.ROTATE_CREDENTIAL if is_credentialed else RecoveryAction.ABORT_WITH_MESSAGE,
            message=str(error),
            tool_name=tool_name,
            retryable=is_credentialed,
            should_rotate_credential=is_credentialed,
            status_code=status_code,
        )

    if _has_pattern(msg, _OVERLOADED_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.overloaded,
            recovery_action=RecoveryAction.BACKOFF_AND_RETRY,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
            status_code=status_code,
            error_context={"retry_after_seconds": 1.0},
        )

    if _has_pattern(msg, _SERVER_ERROR_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.server_error,
            recovery_action=RecoveryAction.BACKOFF_AND_RETRY,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
            status_code=status_code,
            error_context={"retry_after_seconds": 0.5},
        )

    if _has_pattern(msg, _CONTENT_POLICY_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.content_policy_blocked,
            recovery_action=RecoveryAction.ABORT_WITH_MESSAGE,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
            status_code=status_code,
        )

    if _has_pattern(msg, _MODEL_NOT_FOUND_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.model_not_found,
            recovery_action=RecoveryAction.ABORT_WITH_MESSAGE,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
            status_code=status_code,
        )

    if _has_pattern(msg, _VALIDATION_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.validation,
            recovery_action=RecoveryAction.INJECT_SIGNAL,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
            status_code=status_code,
        )

    if _has_pattern(msg, _NOT_FOUND_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.not_found,
            recovery_action=RecoveryAction.INJECT_SIGNAL,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
            status_code=status_code,
        )

    if _has_pattern(msg, _PERMISSION_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.permission,
            recovery_action=RecoveryAction.ABORT_WITH_MESSAGE,
            message=str(error),
            tool_name=tool_name,
            retryable=False,
            status_code=status_code,
        )

    # --- Step 3: Context / payload checks ---

    if _has_pattern(msg, _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.context_overflow,
            recovery_action=RecoveryAction.COMPRESS_CONTEXT,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            status_code=status_code,
        )

    if _has_pattern(msg, _PAYLOAD_TOO_LARGE_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.payload_too_large,
            recovery_action=RecoveryAction.COMPRESS_CONTEXT,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            status_code=status_code,
        )

    if _has_pattern(msg, _IMAGE_TOO_LARGE_PATTERNS):
        return ClassifiedToolError(
            reason=ToolFailoverReason.image_too_large,
            recovery_action=RecoveryAction.RETRY_DIFFERENT,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            status_code=status_code,
        )

    # --- Step 4: Transport error heuristics ---

    if _has_pattern(msg, _TIMEOUT_PATTERNS) or isinstance(
        error, (TimeoutError, ConnectionError, OSError)
    ):
        return ClassifiedToolError(
            reason=ToolFailoverReason.timeout,
            recovery_action=RecoveryAction.RETRY_SAME,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            should_backoff=True,
            status_code=status_code,
        )

    # Large session + disconnect heuristic (like hermes)
    if approx_tokens > 0 and approx_tokens > context_length * 0.6:
        return ClassifiedToolError(
            reason=ToolFailoverReason.context_overflow,
            recovery_action=RecoveryAction.COMPRESS_CONTEXT,
            message=str(error),
            tool_name=tool_name,
            retryable=True,
            status_code=status_code,
            error_context={"approx_tokens": approx_tokens, "context_length": context_length},
        )

    # --- Step 5: Fallback — retryable with backoff (safe default) ---
    return ClassifiedToolError(
        reason=ToolFailoverReason.unknown,
        recovery_action=RecoveryAction.RETRY_SAME,
        message=str(error),
        tool_name=tool_name,
        retryable=True,
        status_code=status_code,
    )


__all__ = [
    "ToolFailoverReason",
    "RecoveryAction",
    "ClassifiedToolError",
    "classify_tool_error",
]
