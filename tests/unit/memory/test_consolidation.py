"""Unit tests for memory consolidation worker."""

import pytest
import time
from unittest.mock import MagicMock

from logicore.memory.types import (
    MemoryMetadata, MemoryHeader, MemoryType, MemoryDomain,
    MemoryKind, MemoryStability,
)
from logicore.memory.consolidation.worker import ConsolidationWorker, should_forget


class TestShouldForget:
    def test_stable_never_forgets(self):
        old_ms = (time.time() - 365 * 24 * 3600 * 10) * 1000
        assert should_forget(old_ms, MemoryStability.STABLE) is False
    
    def test_ephemeral_forgets_quickly(self):
        old_ms = (time.time() - 5 * 24 * 3600) * 1000
        assert should_forget(old_ms, MemoryStability.EPHEMERAL) is True
    
    def test_recent_not_forgotten(self):
        now_ms = time.time() * 1000
        assert should_forget(now_ms, MemoryStability.EVOLVING) is False


class TestConsolidationWorker:
    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.scan_memory_files.return_value = []
        store.read_memory_file.return_value = None
        store.write_memory_file.return_value = True
        store.delete_memory_file.return_value = True
        store.update_index.return_value = True
        store.memory_dir = MagicMock()
        return store
    
    @pytest.fixture
    def worker(self, mock_store):
        return ConsolidationWorker(mock_store, debug=True)
    
    def test_initialization(self, worker):
        assert worker._last_consolidation is None
        assert worker._session_count == 0
    
    def test_should_consolidate_initially(self, worker):
        # No previous consolidation, but need sessions
        assert worker.should_consolidate() is False
    
    def test_should_consolidate_after_sessions(self, worker):
        for _ in range(5):
            worker.record_session()
        assert worker.should_consolidate() is True
    
    def test_should_consolidate_time_gate(self, worker):
        worker._session_count = 10
        worker._last_consolidation = time.time()
        
        # Too soon
        assert worker.should_consolidate() is False
        
        # After 24 hours
        worker._last_consolidation = time.time() - 25 * 3600
        assert worker.should_consolidate() is True
    
    def test_build_memory_list(self, worker):
        headers = [
            MemoryHeader(
                filename="test.md",
                file_path="/path/to/test.md",
                mtime_ms=time.time() * 1000,
                description="Test memory",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
                kind=MemoryKind.FACT,
                importance=0.8,
                stability=MemoryStability.STABLE,
            )
        ]
        
        result = worker._build_memory_list(headers)
        assert "test.md" in result
        assert "identity/fact" in result
    
    def test_rule_based_consolidate_finds_duplicates(self, worker):
        memory_files = {
            "file1.md": {
                "metadata": MemoryMetadata(
                    name="test",
                    description="Same description",
                    type=MemoryType.USER,
                ),
                "body": "Body 1",
            },
            "file2.md": {
                "metadata": MemoryMetadata(
                    name="test2",
                    description="Same description",
                    type=MemoryType.USER,
                ),
                "body": "Body 2",
            },
        }
        
        ops = worker._rule_based_consolidate(memory_files)
        assert "file2.md" in ops["delete"]
    
    @pytest.mark.asyncio
    async def test_consolidate_empty(self, worker, mock_store):
        stats = await worker.consolidate()
        assert stats["status"] == "no_memories"
    
    @pytest.mark.asyncio
    async def test_consolidate_with_memories(self, worker, mock_store):
        headers = [
            MemoryHeader(
                filename="test.md",
                file_path="/path/to/test.md",
                mtime_ms=time.time() * 1000,
                description="Test",
                type=MemoryType.USER,
            )
        ]
        mock_store.scan_memory_files.return_value = headers
        mock_store.read_memory_file.return_value = (
            MemoryMetadata(name="test", description="Test", type=MemoryType.USER),
            "Body",
        )
        
        stats = await worker.consolidate()
        assert stats["status"] == "completed"
        assert worker._last_consolidation is not None
    
    def test_forget_stale(self, worker, mock_store):
        # Create a very old ephemeral memory
        old_ms = (time.time() - 10 * 24 * 3600) * 1000
        headers = [
            MemoryHeader(
                filename="old.md",
                file_path="/path/to/old.md",
                mtime_ms=old_ms,
                description="Old memory",
                type=MemoryType.USER,
                stability=MemoryStability.EPHEMERAL,
            )
        ]
        mock_store.scan_memory_files.return_value = headers
        
        forgotten = worker.forget_stale()
        assert forgotten == 1
        mock_store.delete_memory_file.assert_called_once_with("old.md")
