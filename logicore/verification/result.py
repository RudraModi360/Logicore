"""
Verification result data structures.

Standardized output format for all verifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VerificationIssue:
    """A single issue found during verification.

    Attributes:
        severity: "critical" | "warning" | "info"
        category: Issue classification (alignment, content, format, etc.)
        description: Human-readable description of the issue.
        location: Where in the artifact the issue was found.
        auto_fixable: Whether the auto-fix engine can repair this.
        fix_suggestion: Hint for the LLM or user on how to fix.
    """

    severity: str
    category: str
    description: str
    location: Optional[str] = None
    auto_fixable: bool = False
    fix_suggestion: Optional[str] = None

    def __str__(self) -> str:
        loc = f" [{self.location}]" if self.location else ""
        return f"{self.severity.upper()}{loc}: {self.description}"


@dataclass
class Artifact:
    """A detected artifact created by a tool.

    Attributes:
        path: Absolute path to the artifact file.
        artifact_type: File extension without dot (e.g. "pptx", "pdf").
        size_bytes: File size in bytes.
    """

    path: str
    artifact_type: str
    size_bytes: int = 0

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass
class VerificationResult:
    """Structured output from a verifier.

    Attributes:
        artifact_path: Path to the verified artifact.
        artifact_type: File extension type.
        passed: True if no critical issues (or no issues at all in non-strict).
        confidence: How confident the verifier is (0.0 - 1.0).
        issues: All issues found during verification.
        auto_fixes_applied: Descriptions of fixes the engine applied.
        verification_time_ms: How long verification took.
        skipped: True if verification was skipped.
        skip_reason: Why verification was skipped.
    """

    artifact_path: str
    artifact_type: str
    passed: bool
    confidence: float
    issues: List[VerificationIssue] = field(default_factory=list)
    auto_fixes_applied: List[str] = field(default_factory=list)
    verification_time_ms: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def critical_issues(self) -> List[VerificationIssue]:
        return [i for i in self.issues if i.severity == "critical"]

    @property
    def warning_issues(self) -> List[VerificationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_fixable_issues(self) -> bool:
        return any(i.auto_fixable for i in self.issues)

    def format_for_llm(self) -> str:
        """Format result as a string suitable for LLM consumption."""
        if self.skipped:
            return f"Verification skipped for {self.artifact_path}: {self.skip_reason}"

        lines = [f"**{self.artifact_type.upper()}** ({self.artifact_path})"]
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(f"Confidence: {self.confidence:.0%}")

        if self.auto_fixes_applied:
            lines.append(f"Auto-fixed: {', '.join(self.auto_fixes_applied)}")

        if self.issues:
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)
