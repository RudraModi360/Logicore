"""
Verification configuration.

Controls behavior of the document/artifact verification pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VerificationConfig:
    """Configuration for the verification pipeline.

    Attributes:
        enabled: Master switch - disables all verification when False.
        auto_fix: Attempt to automatically repair fixable issues.
        strict_mode: When True, warnings also count as failures.
                     When False, only critical issues cause failure.
        max_verification_time_ms: Per-artifact timeout in milliseconds.
        skip_for_large_files_mb: Skip visual verification for files larger
                                 than this threshold (in MB). Set to 0 to
                                 never skip.
        user_override: Per-request override. None uses the agent default.
    """

    enabled: bool = True
    auto_fix: bool = True
    strict_mode: bool = False
    max_verification_time_ms: int = 5000
    skip_for_large_files_mb: int = 50
    user_override: Optional[bool] = None

    @classmethod
    def from_env(cls) -> "VerificationConfig":
        """Build config from environment variables (opt-in overrides)."""
        def _raw(key: str, default: str = "") -> str:
            return os.environ.get(key, default).strip()

        enabled_raw = _raw("LOGICORE_VERIFY_OUTPUT")
        auto_fix_raw = _raw("LOGICORE_VERIFY_AUTO_FIX", "true")
        strict_raw = _raw("LOGICORE_VERIFY_STRICT", "false")
        timeout_raw = _raw("LOGICORE_VERIFY_TIMEOUT_MS", "5000")
        large_raw = _raw("LOGICORE_VERIFY_SKIP_LARGE_MB", "50")

        config = cls()
        if enabled_raw:
            config.enabled = enabled_raw.lower() in ("1", "true", "yes")
        if auto_fix_raw:
            config.auto_fix = auto_fix_raw.lower() in ("1", "true", "yes")
        if strict_raw:
            config.strict_mode = strict_raw.lower() in ("1", "true", "yes")
        if timeout_raw:
            try:
                config.max_verification_time_ms = int(timeout_raw)
            except ValueError:
                pass
        if large_raw:
            try:
                config.skip_for_large_files_mb = int(large_raw)
            except ValueError:
                pass
        return config
