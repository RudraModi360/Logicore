"""Integration tests for memory subsystem."""

import pytest
import tempfile
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from logicore.memory.types import (
    MemoryMetadata, MemoryType, MemoryDomain, MemoryKind,
    MemoryStability, MemoryHeader,
)
from logicore.memory.storage import MemoryStore
from logicore.memory.retrieval.retriever import MemoryRetriever
from logicore.memory.extraction.worker import ExtractionWorker
from logicore.memory.consolidation.worker import ConsolidationWorker
from logicore.memory.manager import MemoryManager


class MockLLMProvider:
    """Mock LLM provider for integration tests."""
    
    def __init__(self, extraction_response=None, consolidation_response=None):
        self.extraction_response = extraction_response or "[]"
        self.consolidation_response = consolidation_response or '{"merge": [], "update": [], "delete": [], "create": []}'
        self.extraction_calls = []
        self.consolidation_calls = []
    
    async def chat(self, messages, tools=None):
        # Determine if this is extraction or consolidation
        system_msg = messages[0]["content"] if messages else ""
        
        if "memory extraction" in system_msg.lower():
            self.extraction_calls.append(messages)
            response = MagicMock()
            response.content = self.extraction_response
            return response
        elif "consolidation" in system_msg.lower():
            self.consolidation_calls.append(messages)
            response = MagicMock()
            response.content = self.consolidation_response
            return response
        
        response = MagicMock()
        response.content = "[]"
        return response


class TestMemoryEndToEnd:
    """End-to-end integration tests for memory subsystem."""
    
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def full_setup(self, temp_dir):
        """Set up full memory subsystem."""
        llm = MockLLMProvider()
        manager = MemoryManager(temp_dir, llm_provider=llm, debug=True)
        return manager, llm
    
    @pytest.mark.asyncio
    async def test_write_and_retrieve_memory(self, full_setup):
        manager, llm = full_setup
        
        # Write a memory
        meta = MemoryMetadata(
            name="user_role",
            description="User is a senior engineer",
            type=MemoryType.USER,
            domain=MemoryDomain.IDENTITY,
            kind=MemoryKind.FACT,
            importance=0.9,
            stability=MemoryStability.STABLE,
            tags=["role", "engineer"],
        )
        manager.store.write_memory_file("user_role.md", meta, "Senior backend engineer with 10 years experience")
        
        # Scan and verify
        headers = manager.store.scan_memory_files()
        assert len(headers) == 1
        assert headers[0].filename == "user_role.md"
        
        # Read back
        result = manager.store.read_memory_file(str(manager.store.memory_dir / "user_role.md"))
        assert result is not None
        metadata, body = result
        assert metadata.name == "user_role"
        assert "Senior backend engineer" in body
    
    @pytest.mark.asyncio
    async def test_retrieve_relevant_memories(self, full_setup):
        manager, llm = full_setup
        
        # Write multiple memories
        memories = [
            ("user_role.md", MemoryMetadata(
                name="user_role",
                description="User is a senior engineer",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
                importance=0.9,
            ), "Senior backend engineer"),
            ("user_pref.md", MemoryMetadata(
                name="user_pref",
                description="Prefers concise responses",
                type=MemoryType.FEEDBACK,
                domain=MemoryDomain.PREFERENCES,
                importance=0.7,
            ), "Prefers terse responses with no emojis"),
            ("project.md", MemoryMetadata(
                name="project",
                description="Working on API redesign",
                type=MemoryType.PROJECT,
                domain=MemoryDomain.KNOWLEDGE,
                importance=0.8,
            ), "Redesigning the REST API"),
        ]
        
        for filename, meta, body in memories:
            manager.store.write_memory_file(filename, meta, body)
        
        # Retrieve for identity query
        memories_found = manager.retriever.retrieve("Who am I?")
        assert len(memories_found) > 0
        
        # Should find identity-related memory
        domains = [m[0].domain for m in memories_found]
        assert MemoryDomain.IDENTITY in domains
    
    @pytest.mark.asyncio
    async def test_context_injection(self, full_setup):
        manager, llm = full_setup
        
        # Write a memory
        meta = MemoryMetadata(
            name="user_role",
            description="User prefers dark mode",
            type=MemoryType.FEEDBACK,
            domain=MemoryDomain.PREFERENCES,
            importance=0.8,
        )
        manager.store.write_memory_file("prefs.md", meta, "Always use dark theme")
        
        # Inject context
        messages = [{"role": "user", "content": "Hello"}]
        result = await manager.inject_context(messages, "Hello")
        
        # Should have injection message
        assert len(result) >= 1
    
    @pytest.mark.asyncio
    async def test_extraction_workflow(self, full_setup):
        manager, llm = full_setup
        await manager.start()  # Start the worker before extraction

        # Set up extraction response
        llm.extraction_response = json.dumps([
            {
                "action": "create",
                "filename": "extracted_memory.md",
                "metadata": {
                    "name": "extracted",
                    "description": "Extracted from conversation",
                    "type": "user",
                    "domain": "identity",
                    "kind": "fact",
                    "confidence": 0.85,
                    "importance": 0.7,
                    "stability": "evolving",
                    "tags": ["extracted"],
                },
                "body": "This memory was extracted from the conversation.",
            }
        ])
        
        # Submit conversation
        conversation = [
            {"role": "user", "content": "My name is Alice and I'm a designer"},
            {"role": "assistant", "content": "Hello Alice! Nice to meet you."},
        ]
        
        await manager.submit_for_extraction(conversation, "test-session")
        
        # Wait for extraction to process
        await asyncio.sleep(0.5)
        
        # Verify memory was created
        headers = manager.store.scan_memory_files()
        filenames = [h.filename for h in headers]
        assert "extracted_memory.md" in filenames
    
    @pytest.mark.asyncio
    async def test_consolidation_workflow(self, full_setup):
        manager, llm = full_setup
        
        # Write some memories
        for i in range(3):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
            )
            manager.store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        # Run consolidation
        consolidation_worker = ConsolidationWorker(manager.store, llm, debug=True)
        consolidation_worker._session_count = 10  # Force consolidation
        
        stats = await consolidation_worker.consolidate()
        assert stats["status"] == "completed"
    
    @pytest.mark.asyncio
    async def test_forget_stale_memories(self, full_setup):
        manager, llm = full_setup
        
        # Write a very old ephemeral memory
        import time
        old_ms = (time.time() - 10 * 24 * 3600) * 1000
        
        # Manually write with old timestamp
        meta = MemoryMetadata(
            name="old_memory",
            description="Very old memory",
            type=MemoryType.USER,
            stability=MemoryStability.EPHEMERAL,
        )
        manager.store.write_memory_file("old.md", meta, "Old content")
        
        # Modify the file's mtime to be old
        old_path = manager.store.memory_dir / "old.md"
        os.utime(old_path, (old_ms / 1000, old_ms / 1000))
        
        # Force rescan
        manager.store._headers_cache = None
        
        # Run forget
        consolidation_worker = ConsolidationWorker(manager.store, debug=True)
        forgotten = consolidation_worker.forget_stale()
        
        assert forgotten == 1
        assert not old_path.exists()
    
    def test_memory_index_creation(self, full_setup):
        manager, llm = full_setup
        
        # Write memories in different domains
        memories = [
            ("user.md", MemoryMetadata(
                name="user", description="User info", type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
            )),
            ("pref.md", MemoryMetadata(
                name="pref", description="Preferences", type=MemoryType.FEEDBACK,
                domain=MemoryDomain.PREFERENCES,
            )),
            ("proj.md", MemoryMetadata(
                name="proj", description="Project info", type=MemoryType.PROJECT,
                domain=MemoryDomain.KNOWLEDGE,
            )),
        ]
        
        for filename, meta in memories:
            manager.store.write_memory_file(filename, meta, "Content")
        
        # Update index
        manager.store.update_index()
        
        # Read and verify
        index = manager.store.read_index()
        assert "## identity" in index
        assert "## preferences" in index
        assert "## knowledge" in index


class TestMemoryWithRealFiles:
    """Tests using actual file system operations."""
    
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    def test_file_roundtrip(self, temp_dir):
        """Test writing and reading a memory file."""
        store = MemoryStore(temp_dir)
        
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
            domain=MemoryDomain.IDENTITY,
            kind=MemoryKind.FACT,
            confidence=0.9,
            importance=0.8,
            stability=MemoryStability.STABLE,
            tags=["test", "integration"],
        )
        body = "This is a test memory body with **markdown**."
        
        # Write
        success = store.write_memory_file("test.md", meta, body)
        assert success is True
        
        # Verify file exists
        file_path = Path(temp_dir) / "test.md"
        assert file_path.exists()
        
        # Read
        result = store.read_memory_file(str(file_path))
        assert result is not None
        read_meta, read_body = result
        
        assert read_meta.name == "test"
        assert read_meta.confidence == 0.9
        assert read_meta.tags == ["test", "integration"]
        assert "markdown" in read_body
    
    def test_multiple_files(self, temp_dir):
        """Test multiple memory files."""
        store = MemoryStore(temp_dir)
        
        for i in range(5):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
            )
            store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        headers = store.scan_memory_files()
        assert len(headers) == 5
    
    def test_index_update(self, temp_dir):
        """Test index file creation and update."""
        store = MemoryStore(temp_dir)
        
        for i in range(3):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
            )
            store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        store.update_index()
        
        index_path = Path(temp_dir) / "MEMORY.md"
        assert index_path.exists()
        
        content = index_path.read_text()
        assert "identity" in content
        assert "mem0.md" in content
