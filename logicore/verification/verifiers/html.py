"""
HTML verifier.

Verifies HTML files: structure, tags, links, images, accessibility.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Set

from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.result import VerificationIssue


# Supported HTML extensions.
HTML_EXTENSIONS: Set[str] = {".html", ".htm"}

# Maximum file size for HTML (50 MB).
MAX_HTML_SIZE = 50 * 1024 * 1024


class HTMLVerifier(BaseVerifier):
    """Verify HTML files: structure, tags, links, images, accessibility.

    Checks:
    - Valid HTML structure (doctype, html, head, body)
    - Images have alt text
    - Links are not broken (local)
    - No empty divs/sections
    - Responsive viewport meta tag
    - Proper heading hierarchy
    """

    def supported_extensions(self) -> Set[str]:
        return HTML_EXTENSIONS

    def _verify_content(
        self,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        # Read the HTML content.
        try:
            with open(artifact_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            self._add_issue(
                issues,
                severity="critical",
                category="corruption",
                description=f"Cannot read HTML file: {exc}",
            )
            return

        if not content.strip():
            self._add_issue(
                issues,
                severity="critical",
                category="content",
                description="HTML file is empty",
            )
            return

        # Parse with BeautifulSoup if available, otherwise regex.
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            self._verify_with_bs4(soup, artifact_path, issues, requirements)
        except ImportError:
            self._verify_with_regex(content, artifact_path, issues)

    def _verify_with_bs4(
        self,
        soup,
        artifact_path: str,
        issues: List[VerificationIssue],
        requirements: Optional[str],
    ) -> None:
        """Verify HTML using BeautifulSoup."""
        # Doctype check.
        if not str(soup).lower().startswith("<!doctype"):
            # Check if there's any content before <html>.
            html_tag = soup.find("html")
            if html_tag:
                doctype = soup.find(string=re.compile(r"<!doctype", re.I))
                if not doctype:
                    self._add_issue(
                        issues,
                        severity="warning",
                        category="structure",
                        description="HTML is missing DOCTYPE declaration",
                        auto_fixable=True,
                        fix_suggestion="Add <!DOCTYPE html> at the beginning",
                    )

        # Head section check.
        head = soup.find("head")
        if not head:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description="HTML is missing <head> section",
            )
        else:
            # Title check.
            title = head.find("title")
            if not title or not title.string or not title.string.strip():
                self._add_issue(
                    issues,
                    severity="warning",
                    category="structure",
                    description="HTML is missing <title> or title is empty",
                    auto_fixable=True,
                    fix_suggestion="Add a descriptive title",
                )

            # Viewport meta check.
            viewport = head.find("meta", attrs={"name": "viewport"})
            if not viewport:
                self._add_issue(
                    issues,
                    severity="info",
                    category="structure",
                    description="HTML is missing viewport meta tag (not mobile-friendly)",
                    auto_fixable=True,
                    fix_suggestion='Add <meta name="viewport" content="width=device-width, initial-scale=1">',
                )

        # Body section check.
        body = soup.find("body")
        if not body:
            self._add_issue(
                issues,
                severity="critical",
                category="structure",
                description="HTML is missing <body> section",
            )
            return

        # Content check.
        text_content = body.get_text(strip=True)
        if len(text_content) < 10:
            self._add_issue(
                issues,
                severity="warning",
                category="content",
                description="HTML body has very little text content",
            )

        # Image alt text check.
        images = body.find_all("img")
        missing_alt = []
        for img in images:
            alt = img.get("alt")
            if alt is None or alt.strip() == "":
                src = img.get("src", "unknown")
                missing_alt.append(src)

        if missing_alt:
            self._add_issue(
                issues,
                severity="warning",
                category="content",
                description=f"{len(missing_alt)} image(s) missing alt text",
                auto_fixable=True,
                fix_suggestion="Add alt attributes to all images for accessibility",
            )

        # Local link check.
        broken_links = self._check_local_links(body, artifact_path)
        if broken_links:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description=f"{len(broken_links)} broken local link(s): {broken_links[:5]}",
                auto_fixable=False,
                fix_suggestion="Fix or remove broken links",
            )

        # Heading hierarchy check.
        headings = body.find_all(re.compile(r"^h[1-6]$"))
        if headings:
            self._check_heading_hierarchy(headings, issues)

        # Empty div/section check.
        empty_containers = []
        for tag in body.find_all(["div", "section", "article", "main"]):
            if not tag.get_text(strip=True) and not tag.find_all(["img", "video", "iframe", "canvas"]):
                tag_id = tag.get("id") or tag.get("class", ["unknown"])[0] if tag.get("class") else "unknown"
                empty_containers.append(str(tag_id))

        if empty_containers:
            self._add_issue(
                issues,
                severity="info",
                category="content",
                description=f"{len(empty_containers)} empty container(s) found",
                auto_fixable=True,
                fix_suggestion="Add content to empty containers or remove them",
            )

    def _verify_with_regex(
        self,
        content: str,
        artifact_path: str,
        issues: List[VerificationIssue],
    ) -> None:
        """Verify HTML using regex (fallback when BeautifulSoup not available)."""
        content_lower = content.lower()

        # Doctype check.
        if "<!doctype" not in content_lower:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description="HTML is missing DOCTYPE declaration",
                auto_fixable=True,
            )

        # Basic structure checks.
        if "<html" not in content_lower:
            self._add_issue(
                issues,
                severity="critical",
                category="structure",
                description="HTML is missing <html> tag",
            )

        if "<head" not in content_lower:
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description="HTML is missing <head> section",
            )

        if "<body" not in content_lower:
            self._add_issue(
                issues,
                severity="critical",
                category="structure",
                description="HTML is missing <body> section",
            )

        # Title check.
        title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        if not title_match or not title_match.group(1).strip():
            self._add_issue(
                issues,
                severity="warning",
                category="structure",
                description="HTML is missing <title> or title is empty",
                auto_fixable=True,
            )

        # Image alt text check.
        img_tags = re.findall(r"<img[^>]*>", content, re.I)
        missing_alt = []
        for img in img_tags:
            alt_match = re.search(r'alt="([^"]*)"', img)
            if not alt_match or not alt_match.group(1).strip():
                src_match = re.search(r'src="([^"]*)"', img)
                src = src_match.group(1) if src_match else "unknown"
                missing_alt.append(src)

        if missing_alt:
            self._add_issue(
                issues,
                severity="warning",
                category="content",
                description=f"{len(missing_alt)} image(s) missing alt text",
                auto_fixable=True,
            )

    def _check_local_links(self, body, artifact_path: str) -> List[str]:
        """Check if local links point to existing files."""
        broken = []
        base_dir = os.path.dirname(os.path.abspath(artifact_path))

        for a_tag in body.find_all("a", href=True):
            href = a_tag["href"]
            # Skip external links, anchors, javascript.
            if href.startswith(("http://", "https://", "mailto:", "javascript:", "#")):
                continue

            # Resolve relative path.
            link_path = os.path.join(base_dir, href)
            if not os.path.exists(link_path):
                broken.append(href)

        return broken[:10]  # Limit to first 10.

    def _check_heading_hierarchy(
        self,
        headings,
        issues: List[VerificationIssue],
    ) -> None:
        """Check that headings follow proper hierarchy (h1 -> h2 -> h3)."""
        prev_level = 0
        skipped_levels = []

        for heading in headings:
            tag_name = heading.name
            level = int(tag_name[1])

            if prev_level > 0 and level > prev_level + 1:
                skipped_levels.append(f"h{prev_level}->h{level}")

            prev_level = level

        if skipped_levels:
            self._add_issue(
                issues,
                severity="info",
                category="structure",
                description=f"Heading hierarchy skips levels: {', '.join(skipped_levels[:3])}",
                fix_suggestion="Use sequential heading levels (h1 -> h2 -> h3)",
            )


def get_verifier() -> HTMLVerifier:
    """Return an HTMLVerifier instance for registry auto-discovery."""
    return HTMLVerifier()
