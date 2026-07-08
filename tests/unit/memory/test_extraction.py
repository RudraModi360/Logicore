"""Unit tests for memory extraction worker."""

import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch

from logicore.memory.types import (
    MemoryMetadata, MemoryType, MemoryDomain, MemoryKind,
    MemoryStability,
)
from logicore.memory.extraction.worker import ExtractionWorker


class MockLLMProvider:
    """Mock LLM provider for testing."""
    
    def __init__(self, response_content=None):
        self.response_content = response_content or "[]"
        self.call_count = 0
    
    async def chat(self, messages, tools=None):
        self.call_count += 1
        response = MagicMock()
        response.content = self.response_content
        return response


class TestExtractionWorker:
    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.scan_memory_files.return_value = []
        store.read_memory_file.return_value = None
        store.write_memory_file.return_value = True
        store.update_index.return_value = True
        return store
    
    @pytest.fixture
    def worker(self, mock_store):
        llm = MockLLMProvider()
        return ExtractionWorker(mock_store, llm, debug=True)
    
    def test_initialization(self, worker):
        assert worker._running is False
        assert worker._extraction_count == 0
    
    def test_stats(self, worker):
        stats = worker.get_stats()
        assert stats["running"] is False
        assert stats["queue_size"] == 0
        assert stats["total_extractions"] == 0
    
    def test_build_manifest_empty(self, worker):
        manifest = worker._build_manifest()
        assert manifest == "No existing memories."
    
    def test_build_manifest_with_memories(self, worker):
        from logicore.memory.types import MemoryHeader
        
        headers = [
            MemoryHeader(
                filename="test.md",
                file_path="/path/to/test.md",
                mtime_ms=1000.0,
                description="Test memory",
                type=MemoryType.USER,
                domain=MemoryDomain.IDENTITY,
                kind=MemoryKind.FACT,
                importance=0.8,
                stability=MemoryStability.STABLE,
                tags=["test"],
            )
        ]
        worker.memory_store.scan_memory_files.return_value = headers
        
        manifest = worker._build_manifest()
        assert "test.md" in manifest
        assert "identity/fact" in manifest
    
    def test_format_transcript(self, worker):
        conversation = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        result = worker._format_transcript(conversation)
        assert "user: Hello" in result
        assert "assistant: Hi there!" in result
    
    def test_format_transcript_limits_messages(self, worker):
        conversation = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(30)
        ]
        result = worker._format_transcript(conversation)
        # Should only include last 20 messages
        assert "Message 0" not in result
        assert "Message 29" in result
    
    def test_parse_operations_valid_json(self, worker):
        response = '[{"action": "create", "filename": "test.md"}]'
        ops = worker._parse_operations(response)
        assert len(ops) == 1
        assert ops[0]["action"] == "create"
    
    def test_parse_operations_code_block(self, worker):
        response = '```json\n[{"action": "create"}]\n```'
        ops = worker._parse_operations(response)
        assert len(ops) == 1
    
    def test_parse_operations_invalid_json(self, worker):
        response = "not json at all"
        ops = worker._parse_operations(response)
        assert ops == []
    
    def test_parse_operations_not_array(self, worker):
        response = '{"action": "create"}'
        ops = worker._parse_operations(response)
        assert ops == []
    
    def test_extract_json_from_code_block(self, worker):
        text = 'Here is the result:\n```json\n[1, 2, 3]\n```\nDone.'
        result = worker._extract_json(text)
        assert result == "[1, 2, 3]"
    
    def test_extract_json_raw_array(self, worker):
        text = 'Result: [1, 2, 3] is the answer.'
        result = worker._extract_json(text)
        assert result == "[1, 2, 3]"
    
    def test_extract_json_none(self, worker):
        text = "No JSON here"
        result = worker._extract_json(text)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_create_memory(self, worker, mock_store):
        op = {
            "action": "create",
            "filename": "new.md",
            "metadata": {
                "name": "new",
                "description": "New memory",
                "type": "user",
                "domain": "identity",
                "kind": "fact",
                "confidence": 0.9,
                "importance": 0.8,
                "stability": "stable",
                "tags": ["test"],
            },
            "body": "New body content",
        }
        
        result = await worker._execute_operation(op)
        
        assert result is not None
        assert result.name == "new"
        mock_store.write_memory_file.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_update_memory(self, worker, mock_store):
        existing_meta = MemoryMetadata(
            name="existing",
            description="Old description",
            type=MemoryType.USER,
        )
        mock_store.read_memory_file.return_value = (existing_meta, "Old body")
        
        op = {
            "action": "update",
            "filename": "existing.md",
            "metadata": {"description": "New description"},
            "body": "Updated body",
        }
        
        result = await worker._execute_operation(op)
        
        assert result is not None
        assert result.description == "New description"
    
    @pytest.mark.asyncio
    async def test_delete_memory(self, worker, mock_store):
        op = {"action": "delete", "filename": "old.md"}
        result = await worker._execute_operation(op)
        
        assert result is None
        mock_store.delete_memory_file.assert_called_once_with("old.md")
    
    @pytest.mark.asyncio
    async def test_execute_operation_invalid(self, worker, mock_store):
        # Missing action
        result = await worker._execute_operation({"filename": "test.md"})
        assert result is None
        
        # Missing filename
        result = await worker._execute_operation({"action": "create"})
        assert result is None
    
    @pytest.mark.asyncio
    async def test_extract_memories_success(self, worker, mock_store):
        worker.llm_provider.response_content = json.dumps([
            {
                "action": "create",
                "filename": "extracted.md",
                "metadata": {
                    "name": "extracted",
                    "description": "Extracted memory",
                    "type": "user",
                },
                "body": "Extracted content",
            }
        ])
        
        item = {
            "conversation": [
                {"role": "user", "content": "My name is Alice"},
                {"role": "assistant", "content": "Hello Alice!"},
            ],
            "session_id": "test-session",
        }
        
        await worker._extract_memories(item)
        
        assert worker._extraction_count == 1
        mock_store.update_index.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_extract_memories_empty_response(self, worker, mock_store):
        worker.llm_provider.response_content = "[]"
        
        item = {
            "conversation": [{"role": "user", "content": "Hi"}],
            "session_id": "test",
        }
        
        await worker._extract_memories(item)
        
        assert worker._extraction_count == 0
