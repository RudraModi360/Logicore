"""Unit tests for memory storage."""

import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime

from logicore.memory.types import (
    MemoryMetadata, MemoryType, MemoryDomain, MemoryKind,
    MemoryStability, MemoryHeader,
)
from logicore.memory.storage import (
    parse_frontmatter,
    serialize_frontmatter,
    parse_memory_header,
    validate_memory_path,
    MemoryStore,
)


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = """---
name: test
description: Test memory
type: user
---
Body content here
"""
        fm, body = parse_frontmatter(content)
        assert fm["name"] == "test"
        assert fm["description"] == "Test memory"
        assert fm["type"] == "user"
        assert body.strip() == "Body content here"
    
    def test_no_frontmatter(self):
        content = "Just plain content"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == content
    
    def test_empty_frontmatter(self):
        content = """---
---
Body
"""
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body.strip() == "Body"
    
    def test_invalid_yaml(self):
        content = """---
invalid: yaml: [missing
---
Body
"""
        fm, body = parse_frontmatter(content)
        assert fm == {}
    
    def test_long_frontmatter(self):
        # Frontmatter beyond max lines
        lines = ["---"] + [f"key{i}: value{i}" for i in range(35)] + ["---", "Body"]
        content = "\n".join(lines)
        fm, body = parse_frontmatter(content)
        # Should fail to find closing delimiter within limit
        assert fm == {}


class TestSerializeFrontmatter:
    def test_basic_serialization(self):
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
        )
        body = "Test body content"
        result = serialize_frontmatter(meta, body)
        
        assert result.startswith("---")
        assert "name: test" in result
        assert "Test body content" in result
    
    def test_preserves_body(self):
        meta = MemoryMetadata(
            name="test",
            description="Test",
            type=MemoryType.USER,
        )
        body = "Line 1\nLine 2\nLine 3"
        result = serialize_frontmatter(meta, body)
        assert "Line 1\nLine 2\nLine 3" in result


class TestParseMemoryHeader:
    def test_valid_header(self):
        content = """---
name: test
description: Test memory
type: user
domain: identity
kind: fact
confidence: 0.9
importance: 0.8
stability: stable
tags: [tag1, tag2]
---
"""
        header = parse_memory_header("test.md", "/path/to/test.md", content, 1000.0)
        assert header is not None
        assert header.filename == "test.md"
        assert header.description == "Test memory"
        assert header.type == MemoryType.USER
        assert header.domain == MemoryDomain.IDENTITY
        assert header.kind == MemoryKind.FACT
        assert header.confidence == 0.9
        assert header.importance == 0.8
        assert header.stability == MemoryStability.STABLE
        assert header.tags == ["tag1", "tag2"]
    
    def test_legacy_type_inference(self):
        content = """---
name: test
description: Test
type: feedback
---
"""
        header = parse_memory_header("test.md", "/path/to/test.md", content, 1000.0)
        assert header.domain == MemoryDomain.PREFERENCES
        assert header.kind == MemoryKind.GUIDELINE
    
    def test_invalid_content(self):
        header = parse_memory_header("test.md", "/path/to/test.md", "invalid", 1000.0)
        assert header is None


class TestValidateMemoryPath:
    def test_valid_path(self):
        assert validate_memory_path("/home/user/memory/test.md") is True
    
    def test_relative_path(self):
        assert validate_memory_path("relative/path.md") is True
    
    def test_null_byte(self):
        assert validate_memory_path("/path/\x00/test.md") is False
    
    def test_unc_path(self):
        assert validate_memory_path("\\\\server\\share\\file.md") is False
    
    def test_empty_path(self):
        assert validate_memory_path("") is False


class TestMemoryStore:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def store(self, temp_dir):
        return MemoryStore(temp_dir, debug=True)
    
    def test_ensure_directory(self, temp_dir):
        new_dir = os.path.join(temp_dir, "new_memory")
        store = MemoryStore(new_dir)
        store.ensure_directory()
        assert os.path.exists(new_dir)
    
    def test_write_and_read_memory(self, store):
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
        )
        
        success = store.write_memory_file("test.md", meta, "Test body")
        assert success is True
        
        result = store.read_memory_file(str(store.memory_dir / "test.md"))
        assert result is not None
        metadata, body = result
        assert metadata.name == "test"
        assert body.strip() == "Test body"
    
    def test_scan_memory_files(self, store):
        # Write a few files
        for i in range(3):
            meta = MemoryMetadata(
                name=f"test{i}",
                description=f"Test memory {i}",
                type=MemoryType.USER,
            )
            store.write_memory_file(f"test{i}.md", meta, f"Body {i}")
        
        headers = store.scan_memory_files()
        assert len(headers) == 3
    
    def test_delete_memory_file(self, store):
        meta = MemoryMetadata(
            name="test",
            description="Test",
            type=MemoryType.USER,
        )
        store.write_memory_file("test.md", meta, "Body")
        
        success = store.delete_memory_file("test.md")
        assert success is True
        
        # Verify deleted
        result = store.read_memory_file(str(store.memory_dir / "test.md"))
        assert result is None
    
    def test_update_index(self, store):
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
        )
        store.write_memory_file("test.md", meta, "Body")
        
        success = store.update_index()
        assert success is True
        
        index = store.read_index()
        assert "test" in index
    
    def test_find_by_domain(self, store):
        # Write memories in different domains
        for i, mem_type in enumerate([MemoryType.USER, MemoryType.FEEDBACK, MemoryType.PROJECT]):
            meta = MemoryMetadata(
                name=f"test{i}",
                description=f"Test {i}",
                type=mem_type,
            )
            store.write_memory_file(f"test{i}.md", meta, f"Body {i}")
        
        identity_memories = store.find_by_domain(MemoryDomain.IDENTITY)
        assert len(identity_memories) == 1
        assert identity_memories[0].filename == "test0.md"
    
    def test_cache_invalidation(self, store):
        # Initial scan
        headers = store.scan_memory_files()
        assert len(headers) == 0
        
        # Write a file
        meta = MemoryMetadata(
            name="test",
            description="Test",
            type=MemoryType.USER,
        )
        store.write_memory_file("test.md", meta, "Body")
        
        # Scan should pick up new file (cache invalidated by write)
        headers = store.scan_memory_files()
        assert len(headers) == 1
