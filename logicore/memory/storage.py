"""
Memory storage layer with YAML frontmatter support.

Handles reading/writing memory files with enhanced metadata,
scanning memory directories, and path resolution.
"""

import os
import re
import yaml
import time
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime
import logging

from logicore.memory.types import (
    MemoryMetadata,
    MemoryHeader,
    MemoryType,
    MemoryDomain,
    MemoryKind,
    MemoryStability,
    LEGACY_TYPE_MAPPING,
)

logger = logging.getLogger(__name__)

# Constants
FRONTMATTER_MAX_LINES = 30
MEMORY_INDEX_FILE = "MEMORY.md"
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25000
MAX_FILE_SIZE_CHARS = 40000
FRONTMATTER_DELIMITER = "---"


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter_dict, body_content)
    """
    lines = content.split("\n")

    # Check if file starts with frontmatter delimiter
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        return {}, content

    # Find closing delimiter (within first FRONTMATTER_MAX_LINES)
    end_idx = -1
    for i, line in enumerate(lines[1 : FRONTMATTER_MAX_LINES + 1], start=1):
        if line.strip() == FRONTMATTER_DELIMITER:
            end_idx = i
            break

    if end_idx == -1:
        return {}, content

    # Parse YAML
    frontmatter_str = "\n".join(lines[1:end_idx])
    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError as e:
        logger.warning(f"Failed to parse frontmatter: {e}")
        return {}, content

    body = "\n".join(lines[end_idx + 1 :])
    return frontmatter, body


def serialize_frontmatter(metadata: MemoryMetadata, body: str) -> str:
    """
    Serialize metadata to YAML frontmatter + body.

    Returns:
        Complete file content with frontmatter
    """
    fm = {
        "name": metadata.name,
        "description": metadata.description,
        "type": metadata.type.value,
        "domain": metadata.domain.value,
        "kind": metadata.kind.value,
        "confidence": metadata.confidence,
        "importance": metadata.importance,
        "stability": metadata.stability.value,
        "created": metadata.created.isoformat() if metadata.created else None,
        "updated": metadata.updated.isoformat() if metadata.updated else None,
        "tags": metadata.tags,
        "relatedTo": metadata.related_to,
    }

    # Remove None values
    fm = {k: v for k, v in fm.items() if v is not None}

    # Add expires_at only if set
    if metadata.expires_at:
        fm["expiresAt"] = metadata.expires_at.isoformat()

    yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    return f"{FRONTMATTER_DELIMITER}\n{yaml_str}{FRONTMATTER_DELIMITER}\n{body}"


def parse_memory_header(
    filename: str, file_path: str, content: str, mtime_ms: float
) -> Optional[MemoryHeader]:
    """
    Parse a memory file's frontmatter into a MemoryHeader.

    Returns None if parsing fails.
    """
    frontmatter, _ = parse_frontmatter(content)

    if not frontmatter:
        return None

    # Extract type
    type_str = frontmatter.get("type", "knowledge")
    try:
        mem_type = MemoryType(type_str)
    except ValueError:
        mem_type = MemoryType.PROJECT

    # Extract domain (or infer from type)
    domain_str = frontmatter.get("domain")
    if domain_str:
        try:
            domain = MemoryDomain(domain_str)
        except ValueError:
            domain = LEGACY_TYPE_MAPPING.get(mem_type, (MemoryDomain.KNOWLEDGE,))[0]
    else:
        domain = LEGACY_TYPE_MAPPING.get(mem_type, (MemoryDomain.KNOWLEDGE,))[0]

    # Extract kind (or infer from type)
    kind_str = frontmatter.get("kind")
    if kind_str:
        try:
            kind = MemoryKind(kind_str)
        except ValueError:
            kind = LEGACY_TYPE_MAPPING.get(
                mem_type, (MemoryDomain.KNOWLEDGE, MemoryKind.CONTEXT)
            )[1]
    else:
        kind = LEGACY_TYPE_MAPPING.get(
            mem_type, (MemoryDomain.KNOWLEDGE, MemoryKind.CONTEXT)
        )[1]

    expires_at = None
    if frontmatter.get("expiresAt"):
        try:
            expires_at = datetime.fromisoformat(frontmatter["expiresAt"])
        except (ValueError, TypeError):
            pass

    return MemoryHeader(
        filename=filename,
        file_path=file_path,
        mtime_ms=mtime_ms,
        description=frontmatter.get("description", ""),
        type=mem_type,
        domain=domain,
        kind=kind,
        confidence=frontmatter.get("confidence", 0.7),
        importance=frontmatter.get("importance", 0.5),
        stability=MemoryStability(frontmatter.get("stability", "evolving")),
        expires_at=expires_at,
        tags=frontmatter.get("tags", []),
        related_to=frontmatter.get("relatedTo", []),
    )


def validate_memory_path(path: str) -> bool:
    """
    Validate a memory path for security.

    Rejects:
    - Relative paths
    - Root/near-root paths
    - Null bytes
    - UNC paths
    """
    if not path or "\0" in path:
        return False

    # Check for UNC paths
    if path.startswith("\\\\"):
        return False

    # Normalize
    normalized = os.path.normpath(path)

    # Check for root paths
    if normalized == os.path.dirname(normalized):  # Is root
        return False

    # Check for drive root on Windows
    if len(normalized) <= 3 and ":" in normalized:
        return False

    return True


class MemoryStore:
    """
    Storage layer for persistent memory files.

    Handles reading, writing, scanning, and indexing memory files
    with YAML frontmatter metadata.
    """

    def __init__(self, memory_dir: str, debug: bool = False):
        """
        Initialize the memory store.

        Args:
            memory_dir: Path to memory directory
            debug: Enable debug logging
        """
        self.memory_dir = Path(memory_dir)
        self.debug = debug
        self._headers_cache: Optional[List[MemoryHeader]] = None
        self._cache_mtime: float = 0

    def ensure_directory(self) -> None:
        """Create memory directory if it doesn't exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def scan_memory_files(self, force_refresh: bool = False) -> List[MemoryHeader]:
        """
        Scan memory directory and return headers of all memory files.

        Returns:
            List of MemoryHeader objects, sorted by mtime (newest first)
        """
        # Check cache
        if not force_refresh and self._headers_cache is not None:
            try:
                dir_mtime = os.path.getmtime(self.memory_dir)
                if dir_mtime <= self._cache_mtime:
                    return self._headers_cache
            except OSError:
                pass

        headers = []
        self.ensure_directory()

        for md_file in self.memory_dir.glob("*.md"):
            if md_file.name == MEMORY_INDEX_FILE:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
                mtime_ms = md_file.stat().st_mtime * 1000
                header = parse_memory_header(
                    md_file.name, str(md_file), content, mtime_ms
                )
                if header:
                    headers.append(header)
            except Exception as e:
                if self.debug:
                    logger.debug(f"Failed to parse {md_file.name}: {e}")

        # Sort by mtime descending (newest first)
        headers.sort(key=lambda h: h.mtime_ms, reverse=True)

        # Cap at 200 files
        if len(headers) > 200:
            headers = headers[:200]

        # Update cache
        self._headers_cache = headers
        self._cache_mtime = time.time() * 1000

        return headers

    def read_memory_file(self, file_path: str) -> Optional[Tuple[MemoryMetadata, str]]:
        """
        Read a memory file and return parsed metadata + body.

        Returns:
            Tuple of (MemoryMetadata, body_content) or None if failed
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return None

            content = path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            if not frontmatter:
                return None

            # Parse metadata
            type_str = frontmatter.get("type", "knowledge")
            try:
                mem_type = MemoryType(type_str)
            except ValueError:
                mem_type = MemoryType.PROJECT

            domain_str = frontmatter.get("domain")
            try:
                domain = (
                    MemoryDomain(domain_str)
                    if domain_str
                    else LEGACY_TYPE_MAPPING[mem_type][0]
                )
            except ValueError:
                domain = LEGACY_TYPE_MAPPING[mem_type][0]

            kind_str = frontmatter.get("kind")
            try:
                kind = (
                    MemoryKind(kind_str)
                    if kind_str
                    else LEGACY_TYPE_MAPPING[mem_type][1]
                )
            except ValueError:
                kind = LEGACY_TYPE_MAPPING[mem_type][1]

            stability_str = frontmatter.get("stability", "evolving")
            try:
                stability = MemoryStability(stability_str)
            except ValueError:
                stability = MemoryStability.EVOLVING

            # Parse timestamps
            created = None
            if frontmatter.get("created"):
                try:
                    created = datetime.fromisoformat(frontmatter["created"])
                except (ValueError, TypeError):
                    pass

            updated = None
            if frontmatter.get("updated"):
                try:
                    updated = datetime.fromisoformat(frontmatter["updated"])
                except (ValueError, TypeError):
                    pass

            expires_at = None
            if frontmatter.get("expiresAt"):
                try:
                    expires_at = datetime.fromisoformat(frontmatter["expiresAt"])
                except (ValueError, TypeError):
                    pass

            metadata = MemoryMetadata(
                name=frontmatter.get("name", path.stem),
                description=frontmatter.get("description", ""),
                type=mem_type,
                domain=domain,
                kind=kind,
                confidence=frontmatter.get("confidence", 0.7),
                importance=frontmatter.get("importance", 0.5),
                stability=stability,
                created=created,
                updated=updated,
                expires_at=expires_at,
                tags=frontmatter.get("tags", []),
                related_to=frontmatter.get("relatedTo", []),
            )

            return metadata, body

        except Exception as e:
            if self.debug:
                logger.debug(f"Failed to read {file_path}: {e}")
            return None

    def write_memory_file(
        self, filename: str, metadata: MemoryMetadata, body: str
    ) -> bool:
        """
        Write a memory file with frontmatter.

        Returns:
            True if successful, False otherwise
        """
        try:
            self.ensure_directory()

            file_path = self.memory_dir / filename

            # Validate path
            if not validate_memory_path(str(file_path)):
                logger.error(f"Invalid memory path: {file_path}")
                return False

            # Check file size
            content = serialize_frontmatter(metadata, body)
            if len(content) > MAX_FILE_SIZE_CHARS:
                logger.warning(f"Memory file {filename} exceeds size limit")
                return False

            # Update timestamp
            metadata.updated = datetime.now()

            file_path.write_text(content, encoding="utf-8")

            # Invalidate cache
            self._headers_cache = None

            return True

        except Exception as e:
            if self.debug:
                logger.debug(f"Failed to write {filename}: {e}")
            return False

    def delete_memory_file(self, filename: str) -> bool:
        """Delete a memory file."""
        try:
            file_path = self.memory_dir / filename
            if file_path.exists():
                file_path.unlink()
                self._headers_cache = None
                return True
            return False
        except Exception as e:
            if self.debug:
                logger.debug(f"Failed to delete {filename}: {e}")
            return False

    def update_index(self, headers: Optional[List[MemoryHeader]] = None) -> bool:
        """
        Update MEMORY.md index file.

        Args:
            headers: Optional pre-scanned headers. If None, scans directory.
        """
        try:
            self.ensure_directory()

            if headers is None:
                headers = self.scan_memory_files()

            # Group by domain
            by_domain: Dict[MemoryDomain, List[MemoryHeader]] = {}
            for h in headers:
                domain = h.domain or MemoryDomain.KNOWLEDGE
                if domain not in by_domain:
                    by_domain[domain] = []
                by_domain[domain].append(h)

            # Build index
            lines = ["# Memory Index\n"]

            for domain in MemoryDomain:
                if domain not in by_domain:
                    continue

                domain_memories = by_domain[domain]
                lines.append(f"\n## {domain.value}\n")

                for h in domain_memories:
                    name = h.filename.replace(".md", "")
                    desc = h.description or "No description"
                    link = f"[{name}]({h.filename})"
                    lines.append(f"- {link} — {desc}")

            index_content = "\n".join(lines)

            # Cap size
            if len(index_content) > MAX_INDEX_BYTES:
                index_content = index_content[:MAX_INDEX_BYTES]

            index_path = self.memory_dir / MEMORY_INDEX_FILE
            index_path.write_text(index_content, encoding="utf-8")

            return True

        except Exception as e:
            if self.debug:
                logger.debug(f"Failed to update index: {e}")
            return False

    def read_index(self) -> str:
        """Read the MEMORY.md index file."""
        try:
            index_path = self.memory_dir / MEMORY_INDEX_FILE
            if index_path.exists():
                return index_path.read_text(encoding="utf-8")
            return ""
        except Exception:
            return ""

    def find_related_memories(
        self, tags: List[str], limit: int = 5
    ) -> List[MemoryHeader]:
        """Find memories with overlapping tags."""
        headers = self.scan_memory_files()

        scored = []
        for h in headers:
            overlap = len(set(tags or []) & set(h.tags or []))
            if overlap > 0:
                scored.append((overlap, h))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored[:limit]]

    def find_by_domain(
        self, domain: MemoryDomain, limit: int = 10
    ) -> List[MemoryHeader]:
        """Find memories in a specific domain."""
        headers = self.scan_memory_files()
        return [h for h in headers if h.domain == domain][:limit]
