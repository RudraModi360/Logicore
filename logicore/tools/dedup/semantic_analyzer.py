"""
Semantic Tool Call Analyzer (Layer 2).

Detects when the model calls tools with different arguments but
the same semantic meaning. Prevents redundant executions.

Example: read_file("src/foo.py", start_line=1, end_line=50)
      == read_file("src/foo.py", start_line=1, end_line=50)
         (exact match — handled by Layer 1)

Example: search_files(pattern="TODO", directory="./src")
      == search_files(pattern="todo", directory="./src", case_sensitive=False)
         (semantic match — handled here)
"""

import os
import re
from typing import Any, Dict, Optional, Tuple


class SemanticAnalyzer:
    """
    Analyzes tool calls for semantic equivalence.
    Returns a normalized semantic key for cache lookup.
    """

    # Tools where semantic analysis applies
    SEMANTIC_TOOLS = {
        "read_file",
        "edit_file",
        "search_files",
        "fast_grep",
        "list_files",
        "bash",
    }

    def get_semantic_key(
        self, tool_name: str, args: Dict[str, Any]
    ) -> Optional[str]:
        """
        Generate a semantic key for a tool call.
        Returns None if the tool doesn't support semantic analysis.
        """
        if tool_name not in self.SEMANTIC_TOOLS:
            return None

        try:
            handler = getattr(self, f"_key_{tool_name}", None)
            if handler:
                return handler(args)
        except Exception:
            pass

        return None

    def are_semantically_equivalent(
        self,
        tool_name: str,
        args_a: Dict[str, Any],
        args_b: Dict[str, Any],
    ) -> bool:
        """Check if two tool calls are semantically equivalent."""
        key_a = self.get_semantic_key(tool_name, args_a)
        key_b = self.get_semantic_key(tool_name, args_b)

        if key_a is None or key_b is None:
            return False

        return key_a == key_b

    # --- Per-tool semantic key generators ---

    def _normalize_path(self, path: str) -> str:
        """Normalize a file path for comparison."""
        return os.path.normpath(os.path.abspath(path))

    def _key_read_file(self, args: Dict[str, Any]) -> str:
        """
        read_file: same path + same offset + same limit = same read.
        """
        path = self._normalize_path(args.get("file_path", ""))
        offset = args.get("start_line")
        limit = args.get("end_line")
        return f"read_file::{path}::{offset}::{limit}"

    def _key_edit_file(self, args: Dict[str, Any]) -> str:
        """
        edit_file: same path + same old_text = same edit.
        (If the edit was already applied, re-executing is harmless but wasteful.)
        """
        path = self._normalize_path(args.get("file_path", ""))
        old_text = (args.get("old_text") or "").strip()
        start = args.get("start_line")
        end = args.get("end_line")
        # Use old_text hash for text-based edits, line range for line-based
        if old_text:
            from .hash_engine import hash_content
            return f"edit_file::{path}::text::{hash_content(old_text)}"
        return f"edit_file::{path}::lines::{start}::{end}"

    def _key_search_files(self, args: Dict[str, Any]) -> str:
        """
        search_files: normalize pattern (lowercase, strip), same dir + file_pattern.
        """
        pattern = (args.get("pattern") or "").lower().strip()
        directory = self._normalize_path(args.get("directory") or ".")
        file_pattern = args.get("file_pattern") or "*"
        case_sensitive = args.get("case_sensitive", False)
        pattern_type = args.get("pattern_type") or "substring"

        # For case-insensitive searches, the case of pattern doesn't matter
        if not case_sensitive:
            pattern = pattern.lower()

        return f"search::{pattern}::{directory}::{file_pattern}::{pattern_type}"

    def _key_fast_grep(self, args: Dict[str, Any]) -> str:
        """
        fast_grep: same as search_files but simpler schema.
        """
        keyword = (args.get("keyword") or "").lower().strip()
        directory = self._normalize_path(args.get("directory") or ".")
        file_pattern = args.get("file_pattern") or "*"
        return f"grep::{keyword}::{directory}::{file_pattern}"

    def _key_list_files(self, args: Dict[str, Any]) -> str:
        """
        list_files: same dir + pattern + recursive flag.
        """
        directory = self._normalize_path(args.get("directory") or ".")
        pattern = args.get("pattern") or "*"
        recursive = args.get("recursive", False)
        show_hidden = args.get("show_hidden", False)
        return f"list::{directory}::{pattern}::{recursive}::{show_hidden}"

    def _key_bash(self, args: Dict[str, Any]) -> str:
        """
        bash: normalize command (strip whitespace, collapse spaces).
        """
        command = (args.get("command") or "").strip()
        # Collapse multiple whitespace to single space
        command = re.sub(r"\s+", " ", command)
        return f"bash::{command}"


# Module-level singleton
semantic_analyzer = SemanticAnalyzer()
