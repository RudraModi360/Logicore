"""Per-attempt recovery bookkeeping for the conversation turn loop.

Modeled after hermes-agent's ``TurnRetryState`` (``agent/turn_retry_state.py``).

The main retry loop in ``ChatOrchestrator._handle_llm_error`` makes several
distinct recovery attempts on a single model API call: credential rotation,
context compression, format recovery, etc.

Each of those branches is guarded by a one-shot boolean so it fires at most
once per attempt. This prevents infinite retry of the same fix — the #1 cause
of "blind repetition" in naive agents.

This module is dependency-free so it can be unit-tested in isolation and
imported by the turn loop without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Optional


class TransitionReason(Enum):
    """WHY the retry loop is continuing — makes recovery logic explicit and auditable."""
    
    INITIAL = "initial"
    TOOL_USE = "tool_use"
    CONTEXT_COMPRESSED = "context_compressed"
    CREDENTIAL_ROTATED = "credential_rotated"
    RATE_LIMITED = "rate_limited"
    EMPTY_RESPONSE = "empty_response"
    MAX_OUTPUT_TOKENS_ESCALATED = "max_output_tokens_escalated"
    FORMAT_RECOVERY = "format_recovery"
    PERMISSION_DENIED = "permission_denied"
    STOP_HOOK_BLOCKING = "stop_hook_blocking"


@dataclass
class TurnRetryState:
    """One-shot recovery guards + restart signals for a single API-call attempt.

    A fresh instance is created for each iteration of the outer turn loop.
    Each guard fires its recovery branch at most once; the ``restart_with_*``
    signals are read by the loop after the attempt to decide whether to
    rebuild the request and retry.
    """

    # ── Per-provider OAuth / credential refresh guards ───────────────────
    credential_rotation_attempted: bool = False
    oauth_refresh_attempted: bool = False

    # ── Format / payload recovery guards ─────────────────────────────────
    format_recovery_attempted: bool = False
    thinking_signature_retry_attempted: bool = False
    image_shrink_retry_attempted: bool = False
    multimodal_content_retry_attempted: bool = False
    invalid_encrypted_content_retry_attempted: bool = False

    # ── Transport / rate-limit recovery ──────────────────────────────────
    rate_limit_retry_attempted: bool = False
    transient_error_retry_attempted: bool = False

    # ── Context management recovery ──────────────────────────────────────
    context_compression_attempted: bool = False
    max_output_tokens_escalated: bool = False

    # ── Restart signals (read by the outer loop after the attempt) ───────
    restart_with_compressed_messages: bool = False
    restart_with_length_continuation: bool = False

    # ── Transition tracking ──────────────────────────────────────────────
    transition_reason: TransitionReason = TransitionReason.INITIAL
    transition_history: list = field(default_factory=list)

    # ── Per-recovery-type counters (for circuit breakers) ────────────────
    retry_count: int = 0
    max_retries: int = 3

    def record_transition(self, reason: TransitionReason, detail: Optional[str] = None):
        """Record a recovery transition for auditing."""
        self.transition_history.append({
            "reason": reason.value,
            "detail": detail,
            "retry_count": self.retry_count,
        })
        self.transition_reason = reason

    def can_retry(self) -> bool:
        """Check if we haven't exhausted retry budget."""
        return self.retry_count < self.max_retries

    def increment_retry(self):
        """Increment the retry counter."""
        self.retry_count += 1

    def mark_credential_rotation(self):
        """Mark credential rotation as attempted."""
        self.credential_rotation_attempted = True

    def mark_rate_limit_retry(self):
        """Mark rate limit retry as attempted."""
        self.rate_limit_retry_attempted = True

    def mark_context_compression(self):
        """Mark context compression as attempted."""
        self.context_compression_attempted = True

    def mark_format_recovery(self):
        """Mark format recovery as attempted."""
        self.format_recovery_attempted = True

    def mark_max_output_tokens(self):
        """Mark max output tokens escalation as attempted."""
        self.max_output_tokens_escalated = True

    def mark_thinking_signature_retry(self):
        """Mark thinking signature retry as attempted."""
        self.thinking_signature_retry_attempted = True

    def mark_image_shrink_retry(self):
        """Mark image shrink retry as attempted."""
        self.image_shrink_retry_attempted = True

    def mark_multimodal_content_retry(self):
        """Mark multimodal content retry as attempted."""
        self.multimodal_content_retry_attempted = True

    def mark_invalid_encrypted_content_retry(self):
        """Mark invalid encrypted content retry as attempted."""
        self.invalid_encrypted_content_retry_attempted = True

    def mark_transient_error_retry(self):
        """Mark transient error retry as attempted."""
        self.transient_error_retry_attempted = True

    def mark_oauth_refresh(self):
        """Mark OAuth refresh as attempted."""
        self.oauth_refresh_attempted = True

    def get_all_flags(self) -> dict:
        """Get all one-shot flags for debugging."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in ("transition_history", "transition_reason")
        }

    def __iter__(self):
        """Convenience for debugging: iterate (name, value) pairs."""
        for f in fields(self):
            yield f.name, getattr(self, f.name)
