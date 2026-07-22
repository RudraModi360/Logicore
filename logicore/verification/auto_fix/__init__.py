"""
Auto-fix engine.

Repairs common issues found during verification automatically.
Each fix is registered as a handler that maps (category, issue_pattern) -> fix function.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

from logicore.verification.result import VerificationIssue, VerificationResult

logger = logging.getLogger(__name__)

# Fix function signature: (artifact_path, issue) -> bool
FixHandler = Callable[[str, VerificationIssue], bool]


class AutoFixEngine:
    """Attempts to automatically fix verification issues.

    Usage::

        engine = AutoFixEngine()
        fixed_result = await engine.fix(verification_result)
    """

    def __init__(self) -> None:
        self._handlers: Dict[Tuple[str, str], FixHandler] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load all built-in fix handlers."""
        if self._loaded:
            return
        self._loaded = True

        _fix_modules = [
            "logicore.verification.auto_fix.fixes.image_fixes",
            "logicore.verification.auto_fix.fixes.pdf_fixes",
            "logicore.verification.auto_fix.fixes.docx_fixes",
            "logicore.verification.auto_fix.fixes.pptx_fixes",
            "logicore.verification.auto_fix.fixes.xlsx_fixes",
            "logicore.verification.auto_fix.fixes.html_fixes",
        ]

        for module_path in _fix_modules:
            try:
                import importlib
                mod = importlib.import_module(module_path)
                register_fn = getattr(mod, "register_fixes", None)
                if register_fn:
                    register_fn(self)
            except Exception as exc:
                logger.debug(f"Could not load fixes from {module_path}: {exc}")

    def register_fix(
        self,
        category: str,
        issue_pattern: str,
        handler: FixHandler,
    ) -> None:
        """Register a fix handler for a specific issue category/pattern.

        Args:
            category: Issue category (e.g. "alignment", "content", "structure")
            issue_pattern: Substring to match in issue description
            handler: Function that fixes the issue. Returns True if successful.
        """
        self._handlers[(category, issue_pattern)] = handler

    def can_fix(self, issue: VerificationIssue) -> bool:
        """Check if we have a handler for this issue."""
        self._ensure_loaded()

        if not issue.auto_fixable:
            return False

        for (cat, pattern), _ in self._handlers.items():
            if cat == issue.category and pattern.lower() in issue.description.lower():
                return True

        return False

    def fix(self, result: VerificationResult) -> VerificationResult:
        """Attempt to fix all fixable issues in a verification result.

        Returns a new VerificationResult with fixes applied.
        """
        self._ensure_loaded()

        if result.passed and not result.issues:
            return result

        fixed_issues: List[VerificationIssue] = []
        applied_fixes: List[str] = []
        unfixed_issues: List[VerificationIssue] = []

        for issue in result.issues:
            if not issue.auto_fixable:
                unfixed_issues.append(issue)
                continue

            handler = self._find_handler(issue)
            if handler is None:
                unfixed_issues.append(issue)
                continue

            try:
                success = handler(result.artifact_path, issue)
                if success:
                    applied_fixes.append(issue.description)
                    logger.debug(f"Auto-fixed: {issue.description}")
                else:
                    unfixed_issues.append(issue)
            except Exception as exc:
                logger.debug(f"Fix failed for '{issue.description}': {exc}")
                unfixed_issues.append(issue)

        # Re-run generic checks after fixes to verify.
        passed = not any(i.severity == "critical" for i in unfixed_issues)

        return VerificationResult(
            artifact_path=result.artifact_path,
            artifact_type=result.artifact_type,
            passed=passed,
            confidence=result.confidence if passed else 0.7,
            issues=unfixed_issues,
            auto_fixes_applied=applied_fixes,
            verification_time_ms=result.verification_time_ms,
        )

    def _find_handler(self, issue: VerificationIssue) -> Optional[FixHandler]:
        """Find a handler for the given issue."""
        for (cat, pattern), handler in self._handlers.items():
            if cat == issue.category and pattern.lower() in issue.description.lower():
                return handler
        return None
