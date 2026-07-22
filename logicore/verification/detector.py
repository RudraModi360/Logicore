"""
Artifact detector.

Scans tool execution results and logs to find created artifacts
that should be verified before returning to the user.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Set

from logicore.verification.result import Artifact


# File extensions we consider artifacts worth verifying.
ARTIFACT_EXTENSIONS: Set[str] = {
    # Office documents
    ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".ppsx",
    # Portable documents
    ".pdf",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg",
    # Web
    ".html", ".htm",
}

# Patterns that indicate a tool created a file.
# Group 1 = file path.
_FILE_PATH_PATTERNS = [
    re.compile(r'(?:created|wrote|saved|generated|exported|output)\s+(?:file\s+)?[`"\']?([^\s`"\']+\.\w{2,5})[`"\']?', re.I),
    re.compile(r'(?:to|into|at)\s+[`"\']?([^\s`"\']+\.\w{2,5})[`"\']?', re.I),
    re.compile(r'(?:file|path)\s*[=:]\s*[`"\']?([^\s`"\']+\.\w{2,5})[`"\']?', re.I),
    re.compile(r'[`"\']([^\s`"\']+[/\\][^\s`"\']+\.\w{2,5})[`"\']', re.I),
]

# Tool metadata keys that may contain the output path.
_OUTPUT_PATH_KEYS = ["output_path", "file_path", "path", "output_file", "result_path"]


class ArtifactDetector:
    """Detects artifacts created by tool execution.

    Usage::

        detector = ArtifactDetector()
        artifacts = detector.detect_from_tool_result("create_pptx", result)
    """

    def detect_from_tool_result(
        self,
        tool_name: str,
        result: object,
        *,
        extra_metadata: Optional[dict] = None,
    ) -> List[Artifact]:
        """Scan a single tool result for created artifacts.

        Args:
            tool_name: Name of the tool that was executed.
            result: The tool result (dict, string, or object with attributes).
            extra_metadata: Optional metadata dict from the tool call.

        Returns:
            List of detected Artifact instances.
        """
        artifacts: List[Artifact] = []

        # Collect all text-like sources to scan.
        text_sources: List[str] = []
        metadata = extra_metadata or {}

        if isinstance(result, dict):
            text_sources.append(str(result))
            for key in _OUTPUT_PATH_KEYS:
                val = result.get(key)
                if val:
                    text_sources.append(str(val))
                    metadata[key] = val
        elif isinstance(result, str):
            text_sources.append(result)
        elif result is not None:
            text_sources.append(str(result))

        # Check metadata for explicit paths.
        for key in _OUTPUT_PATH_KEYS:
            val = metadata.get(key)
            if val and isinstance(val, str):
                artifact = self._path_to_artifact(val)
                if artifact:
                    artifacts.append(artifact)

        # Scan text sources for file paths.
        full_text = "\n".join(text_sources)
        for pattern in _FILE_PATH_PATTERNS:
            for match in pattern.finditer(full_text):
                path = match.group(1)
                artifact = self._path_to_artifact(path)
                if artifact and artifact.path not in [a.path for a in artifacts]:
                    artifacts.append(artifact)

        # Heuristic: tool name suggests file creation.
        if not artifacts and self._tool_creates_file(tool_name):
            # Look for any path-like string in the text.
            for line in full_text.splitlines():
                candidate = line.strip().strip('"').strip("'")
                artifact = self._path_to_artifact(candidate)
                if artifact and artifact.path not in [a.path for a in artifacts]:
                    artifacts.append(artifact)
                    break  # One is enough for heuristic match.

        return artifacts

    def detect_from_execution_log(
        self,
        execution_log: List[str],
    ) -> List[Artifact]:
        """Scan execution log entries for artifact creation patterns.

        Args:
            execution_log: List of log strings from agent.execution_log.

        Returns:
            List of detected Artifact instances.
        """
        artifacts: List[Artifact] = []
        seen: Set[str] = set()

        for entry in execution_log:
            if not isinstance(entry, str):
                continue
            for pattern in _FILE_PATH_PATTERNS:
                for match in pattern.finditer(entry):
                    path = match.group(1)
                    artifact = self._path_to_artifact(path)
                    if artifact and artifact.path not in seen:
                        artifacts.append(artifact)
                        seen.add(artifact.path)

        return artifacts

    def detect_from_message_content(self, content: str) -> List[Artifact]:
        """Scan assistant message content for artifact references.

        Useful for catching artifacts mentioned in the final response.
        """
        artifacts: List[Artifact] = []
        seen: Set[str] = set()

        for pattern in _FILE_PATH_PATTERNS:
            for match in pattern.finditer(content):
                path = match.group(1)
                artifact = self._path_to_artifact(path)
                if artifact and artifact.path not in seen:
                    artifacts.append(artifact)
                    seen.add(artifact.path)

        return artifacts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_to_artifact(self, path: str) -> Optional[Artifact]:
        """Convert a string path to an Artifact if it looks valid."""
        path = path.strip().strip('"').strip("'")
        if not path:
            return None

        ext = os.path.splitext(path)[1].lower()
        if ext not in ARTIFACT_EXTENSIONS:
            return None

        # Resolve to absolute if relative.
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        # Get file size if it exists.
        size = 0
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                pass

        return Artifact(
            path=path,
            artifact_type=ext.lstrip("."),
            size_bytes=size,
        )

    @staticmethod
    def _tool_creates_file(tool_name: str) -> bool:
        """Heuristic: does the tool name suggest it creates files?"""
        indicators = [
            "create", "write", "save", "generate", "export",
            "convert", "build", "render", "compile", "make",
        ]
        name_lower = tool_name.lower()
        return any(ind in name_lower for ind in indicators)
