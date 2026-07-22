"""
Base verifier interface.

All type-specific verifiers inherit from BaseVerifier.
Provides shared utility methods and defines the contract.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Set

from logicore.verification.result import Artifact, VerificationIssue, VerificationResult


class BaseVerifier(ABC):
    """Abstract base class for artifact verifiers.

    Subclasses must implement:
    - ``supported_extensions()``: which file types this verifier handles.
    - ``_verify_content()``: type-specific verification logic.

    The ``verify()`` method orchestrates generic checks then delegates
    to ``_verify_content()``.
    """

    @abstractmethod
    def supported_extensions(self) -> Set[str]:
        """Return the set of file extensions this verifier handles.

        Example: ``{".pptx", ".ppt"}``
        """

    @abstractmethod
    def _verify_content(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        """Run type-specific verification, appending to *issues* in place.

        Args:
            artifact_path: Absolute path to the artifact file.
            issues: Mutable list to append issues to.
            requirements: Original user request text for context.
        """

    def can_verify(self, artifact_type: str) -> bool:
        """Return True if this verifier handles the given type."""
        ext = f".{artifact_type}" if not artifact_type.startswith(".") else artifact_type
        return ext.lower() in self.supported_extensions()

    def verify(
        self,
        artifact_path: str,
        requirements: Optional[str] = None,
    ) -> VerificationResult:
        """Run full verification: generic checks + type-specific content checks.

        Args:
            artifact_path: Absolute path to the artifact file.
            requirements: Original user request for context.

        Returns:
            VerificationResult with all issues found.
        """
        start = time.monotonic()
        issues: List[VerificationIssue] = []

        # Determine type from extension.
        ext = os.path.splitext(artifact_path)[1].lower()
        artifact_type = ext.lstrip(".")

        # --- Generic checks (shared by all verifiers) ---
        self._generic_checks(artifact_path, issues)

        # --- Type-specific checks ---
        # Only run content checks if generic checks passed (file exists/readable).
        file_ok = not any(i.severity == "critical" and i.category == "structure" for i in issues)
        if file_ok:
            try:
                self._verify_content(artifact_path, issues, requirements)
            except Exception as exc:
                issues.append(VerificationIssue(
                    severity="critical",
                    category="corruption",
                    description=f"Verifier raised an exception: {exc}",
                ))

        elapsed_ms = int((time.monotonic() - start) * 1000)
        passed = not any(i.severity == "critical" for i in issues)

        return VerificationResult(
            artifact_path=artifact_path,
            artifact_type=artifact_type,
            passed=passed,
            confidence=0.9 if passed else 0.7,
            issues=issues,
            verification_time_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Generic checks (shared by all verifiers)
    # ------------------------------------------------------------------

    def _generic_checks(self, artifact_path: str, issues: List[VerificationIssue]) -> None:
        """Run base checks: existence, readability, size, magic bytes."""

        # 1. File exists
        if not os.path.exists(artifact_path):
            issues.append(VerificationIssue(
                severity="critical",
                category="structure",
                description="File does not exist",
            ))
            return  # Can't check further.

        # 2. Is a file (not a directory)
        if not os.path.isfile(artifact_path):
            issues.append(VerificationIssue(
                severity="critical",
                category="structure",
                description="Path is not a file",
            ))
            return

        # 3. Readable
        if not os.access(artifact_path, os.R_OK):
            issues.append(VerificationIssue(
                severity="critical",
                category="structure",
                description="File is not readable (permission denied)",
            ))
            return

        # 4. Not empty
        size = os.path.getsize(artifact_path)
        if size == 0:
            issues.append(VerificationIssue(
                severity="critical",
                category="content",
                description="File is empty (0 bytes)",
            ))
            return

        # 5. Reasonable size warning
        if size > 100 * 1024 * 1024:
            issues.append(VerificationIssue(
                severity="warning",
                category="structure",
                description=f"File is unusually large ({size // (1024*1024)} MB)",
            ))

    # ------------------------------------------------------------------
    # Utility helpers for subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def _read_magic_bytes(path: str, n: int = 16) -> bytes:
        """Read the first *n* bytes of a file."""
        try:
            with open(path, "rb") as fh:
                return fh.read(n)
        except OSError:
            return b""

    @staticmethod
    def _add_issue(
        issues: List[VerificationIssue],
        severity: str,
        category: str,
        description: str,
        location: Optional[str] = None,
        auto_fixable: bool = False,
        fix_suggestion: Optional[str] = None,
    ) -> None:
        """Convenience wrapper to append an issue."""
        issues.append(VerificationIssue(
            severity=severity,
            category=category,
            description=description,
            location=location,
            auto_fixable=auto_fixable,
            fix_suggestion=fix_suggestion,
        ))
