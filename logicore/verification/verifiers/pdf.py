"""
PDF verifier.

Verifies PDF files: structure, page count, text extraction, corruption.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set

from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.result import VerificationIssue


# Supported PDF extensions.
PDF_EXTENSIONS: Set[str] = {".pdf"}

# Maximum pages for a reasonable PDF.
MAX_PAGES = 500

# Minimum pages for a expected document.
MIN_PAGES = 1


class PDFVerifier(BaseVerifier):
    """Verify PDF files: structure, page count, text extraction, corruption.

    Checks:
    - PDF header is valid
    - File can be parsed without errors
    - Page count is reasonable
    - Text is extractable (not just scanned images)
    - No encrypted/protected documents
    """

    def supported_extensions(self) -> Set[str]:
        return PDF_EXTENSIONS

    def _verify_content(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        # Try PyPDF2 first, then fallback to basic checks.
        if not self._verify_with_pypdf2(artifact_path, issues):
            self._verify_basic(artifact_path, issues)

    def _verify_with_pypdf2(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
    ) -> bool:
        """Verify using PyPDF2 library. Returns True if verification was done."""
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            try:
                from pypdf import PdfReader
            except ImportError:
                return False

        try:
            reader = PdfReader(artifact_path)
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Cannot parse PDF: {exc}",
                auto_fixable=False,
            )
            return True

        # Page count check.
        try:
            num_pages = len(reader.pages)
        except Exception:
            num_pages = 0

        if num_pages == 0:
            self._add_issue(
                issues,
                severity="critical",
                category="content",
                description="PDF has no pages",
            )
            return True

        if num_pages > MAX_PAGES:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description=f"PDF has {num_pages} pages (unusually large)",
                auto_fixable=False,
                fix_suggestion="Consider splitting into smaller documents",
            )

        # Encryption check.
        try:
            if reader.is_encrypted:
                self._add_issue(
                    issues,
                    severity="warning",
                    category="structure",
                    description="PDF is encrypted/password-protected",
                    auto_fixable=False,
                    fix_suggestion="Remove encryption before sharing",
                )
        except Exception:
            pass

        # Text extraction check (sample first page).
        try:
            if num_pages > 0:
                first_page = reader.pages[0]
                text = first_page.extract_text() or ""
                if len(text.strip()) < 10:
                    self._add_issue(
                        issues,
                        severity="info",
                        category="content",
                        description="First page has little or no extractable text (may be image-based)",
                    )
        except Exception:
            pass

        return True

    def _verify_basic(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
    ) -> None:
        """Basic PDF verification without libraries."""
        try:
            with open(artifact_path, "rb") as f:
                header = f.read(1024)
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Cannot read PDF file: {exc}",
            )
            return

        # Check PDF header.
        if not header.startswith(b"%PDF"):
            self._add_issue(
                issues,
                severity="critical",
                category="format",
                description="File does not have a valid PDF header",
            )
            return

        # Check for EOF marker.
        try:
            with open(artifact_path, "rb") as f:
                # Read last 1024 bytes.
                f.seek(max(0, os.path.getsize(artifact_path) - 1024))
                tail = f.read()
            if b"%%EOF" not in tail:
                self._add_issue(
                    issues,
                    severity="warning",
                    category="corruption",
                    description="PDF may be truncated (no %%EOF marker found)",
                )
        except Exception:
            pass

        # Count pages via grep (basic heuristic).
        try:
            with open(artifact_path, "rb") as f:
                content = f.read()
            page_count = content.count(b"/Type /Page") - content.count(b"/Type /Pages")
            if page_count == 0:
                self._add_issue(
                    issues,
                    severity="warning",
                    category="content",
                    description="Could not detect any pages in PDF",
                )
            elif page_count > MAX_PAGES:
                self._add_issue(
                    issues,
                    severity="warning",
                    category="structure",
                    description=f"PDF has approximately {page_count} pages (unusually large)",
                )
        except Exception:
            pass


def get_verifier() -> PDFVerifier:
    """Return a PDFVerifier instance for registry auto-discovery."""
    return PDFVerifier()
