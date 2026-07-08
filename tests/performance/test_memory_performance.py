"""Performance tests for memory subsystem."""

import pytest
import tempfile
import time
from pathlib import Path

from logicore.memory.types import (
    MemoryMetadata, MemoryType, MemoryDomain, MemoryKind,
    MemoryStability, MemoryHeader,
)
from logicore.memory.storage import MemoryStore
from logicore.memory.retrieval.retriever import (
    MemoryRetriever, detect_topic, compute_decay_score, compute_ranking_score,
)


class TestTopicDetectionPerformance:
    """Performance tests for topic detection."""
    
    def test_topic_detection_speed(self):
        """Ensure topic detection is fast (< 1ms)."""
        queries = [
            "Who am I?",
            "What's the project status?",
            "I prefer dark mode",
            "Tell me about my team",
            "Create a new feature",
        ]
        
        start = time.time()
        for _ in range(1000):
            for query in queries:
                detect_topic(query)
        elapsed = time.time() - start
        
        # 5000 calls should complete in < 1 second
        assert elapsed < 1.0, f"Topic detection too slow: {elapsed:.3f}s for 5000 calls"
    
    def test_decay_score_computation_speed(self):
        """Ensure decay score computation is fast."""
        now_ms = time.time() * 1000
        
        start = time.time()
        for _ in range(10000):
            compute_decay_score(now_ms, 0.5, MemoryStability.EVOLVING)
        elapsed = time.time() - start
        
        # 10000 calls should complete in < 0.5 seconds
        assert elapsed < 0.5, f"Decay computation too slow: {elapsed:.3f}s"
    
    def test_ranking_score_computation_speed(self):
        """Ensure ranking score computation is fast."""
        start = time.time()
        for _ in range(10000):
            compute_ranking_score(0.8, 0.7, 0.9)
        elapsed = time.time() - start
        
        # 10000 calls should complete in < 0.2 seconds
        assert elapsed < 0.2, f"Ranking computation too slow: {elapsed:.3f}s"


class TestStoragePerformance:
    """Performance tests for memory storage."""
    
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def store(self, temp_dir):
        return MemoryStore(temp_dir)
    
    def test_write_performance(self, store):
        """Ensure memory write is fast."""
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
        )
        
        start = time.time()
        for i in range(100):
            meta.name = f"test{i}"
            store.write_memory_file(f"test{i}.md", meta, f"Content {i}")
        elapsed = time.time() - start
        
        # 100 writes should complete in < 2 seconds
        assert elapsed < 2.0, f"Write performance too slow: {elapsed:.3f}s for 100 files"
    
    def test_scan_performance(self, store, temp_dir):
        """Ensure memory scan is fast."""
        # Create 100 memory files
        for i in range(100):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
            )
            store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        # Clear cache
        store._headers_cache = None
        
        start = time.time()
        for _ in range(10):
            store.scan_memory_files(force_refresh=True)
        elapsed = time.time() - start
        
        # 10 scans of 100 files should complete in < 2 seconds
        assert elapsed < 2.0, f"Scan performance too slow: {elapsed:.3f}s"
    
    def test_read_performance(self, store):
        """Ensure memory read is fast."""
        # Create test file
        meta = MemoryMetadata(
            name="test",
            description="Test memory",
            type=MemoryType.USER,
        )
        store.write_memory_file("test.md", meta, "Test content")
        
        file_path = str(store.memory_dir / "test.md")
        
        start = time.time()
        for _ in range(1000):
            store.read_memory_file(file_path)
        elapsed = time.time() - start
        
        # 1000 reads should complete in < 1 second
        assert elapsed < 1.0, f"Read performance too slow: {elapsed:.3f}s"


class TestRetrievalPerformance:
    """Performance tests for memory retrieval."""
    
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def populated_retriever(self, temp_dir):
        store = MemoryStore(temp_dir)
        retriever = MemoryRetriever(store)
        
        # Create 50 memories
        for i in range(50):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY if i % 2 == 0 else MemoryDomain.KNOWLEDGE,
                importance=i / 50.0,
                tags=[f"tag{i}", f"group{i % 5}"],
            )
            store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        return retriever
    
    def test_retrieval_speed(self, populated_retriever):
        """Ensure retrieval is fast."""
        start = time.time()
        for _ in range(100):
            populated_retriever.retrieve("Who am I?")
        elapsed = time.time() - start
        
        # 100 retrievals should complete in < 2 seconds
        assert elapsed < 2.0, f"Retrieval too slow: {elapsed:.3f}s"
    
    def test_retrieval_with_many_memories(self, temp_dir):
        """Test retrieval with 200 memories (at cap)."""
        store = MemoryStore(temp_dir)
        retriever = MemoryRetriever(store)
        
        # Create 200 memories
        for i in range(200):
            meta = MemoryMetadata(
                name=f"memory{i}",
                description=f"Memory {i}",
                type=MemoryType.USER,
                domain=list(MemoryDomain)[i % 6],
            )
            store.write_memory_file(f"mem{i}.md", meta, f"Content {i}")
        
        start = time.time()
        results = retriever.retrieve("What's the project status?")
        elapsed = time.time() - start
        
        # Should complete in < 1 second even with 200 memories
        assert elapsed < 1.0, f"Retrieval with 200 memories too slow: {elapsed:.3f}s"
        # Should return at most 5 results
        assert len(results) <= 5


class TestMemoryFilePerformance:
    """Performance tests for memory file operations."""
    
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    def test_large_memory_file(self, temp_dir):
        """Test handling of large memory files."""
        store = MemoryStore(temp_dir)
        
        # Create a large body (near the 40KB limit)
        large_body = "x" * 35000
        
        meta = MemoryMetadata(
            name="large",
            description="Large memory",
            type=MemoryType.USER,
        )
        
        start = time.time()
        store.write_memory_file("large.md", meta, large_body)
        write_time = time.time() - start
        
        start = time.time()
        result = store.read_memory_file(str(store.memory_dir / "large.md"))
        read_time = time.time() - start
        
        assert result is not None
        assert write_time < 0.5, f"Large file write too slow: {write_time:.3f}s"
        assert read_time < 0.5, f"Large file read too slow: {read_time:.3f}s"
    
    def test_concurrent_reads(self, temp_dir):
        """Test concurrent read operations."""
        import concurrent.futures
        
        store = MemoryStore(temp_dir)
        
        # Create test files
        for i in range(10):
            meta = MemoryMetadata(
                name=f"test{i}",
                description=f"Test {i}",
                type=MemoryType.USER,
            )
            store.write_memory_file(f"test{i}.md", meta, f"Content {i}")
        
        def read_file(idx):
            return store.read_memory_file(str(store.memory_dir / f"test{idx}.md"))
        
        start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(read_file, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        elapsed = time.time() - start
        
        assert all(r is not None for r in results)
        assert elapsed < 1.0, f"Concurrent reads too slow: {elapsed:.3f}s"
