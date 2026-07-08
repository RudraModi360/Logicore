"""Unit tests for memory manager."""

import pytest
import tempfile
import os
from unittest.mock import MagicMock

from logicore.memory.manager import (
    MemoryManager, get_memory_manager, reset_memory_manager,
    resolve_memory_dir, DEFAULT_MEMORY_DIR, MEMORY_DIR_ENV_VAR,
)


class TestResolveMemoryDir:
    def test_default_path(self):
        result = resolve_memory_dir()
        assert result == DEFAULT_MEMORY_DIR
        assert ".logicore" in result
        assert "memory" in result
    
    def test_custom_path(self):
        result = resolve_memory_dir("/custom/path")
        assert result == "/custom/path"
    
    def test_env_var(self, monkeypatch):
        monkeypatch.setenv(MEMORY_DIR_ENV_VAR, "/env/memory")
        result = resolve_memory_dir()
        assert result == "/env/memory"
    
    def test_custom_path_over_env(self, monkeypatch):
        monkeypatch.setenv(MEMORY_DIR_ENV_VAR, "/env/memory")
        result = resolve_memory_dir("/custom/path")
        assert result == "/custom/path"
    
    def test_default_is_global(self):
        home = os.path.expanduser("~")
        assert DEFAULT_MEMORY_DIR.startswith(home)
        assert ".logicore" in DEFAULT_MEMORY_DIR


class TestMemoryManager:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def manager(self, temp_dir):
        return MemoryManager(temp_dir, debug=True)
    
    def test_initialization(self, manager):
        assert manager.enabled is True
        assert manager.store is not None
        assert manager.retriever is not None
        assert manager.memory_dir is not None
    
    def test_default_memory_dir(self):
        manager = MemoryManager()
        assert ".logicore" in manager.memory_dir
        assert "memory" in manager.memory_dir
    
    def test_custom_memory_dir(self, temp_dir):
        custom = os.path.join(temp_dir, "custom_memory")
        manager = MemoryManager(custom)
        assert manager.memory_dir == custom
    
    def test_disabled_manager(self):
        manager = MemoryManager("/tmp/test", enabled=False)
        assert manager.enabled is False
        assert manager.store is None
        assert manager.retriever is None
    
    def test_memory_prompt_section(self, manager):
        section = manager.get_memory_prompt_section()
        assert "Persistent Memory" in section
        assert "MEMORY TYPES" in section
    
    def test_disabled_memory_prompt(self):
        manager = MemoryManager("/tmp/test", enabled=False)
        section = manager.get_memory_prompt_section()
        assert section == ""
    
    @pytest.mark.asyncio
    async def test_inject_context_no_memories(self, manager):
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        result = await manager.inject_context(messages, "Hello")
        # No memories to inject
        assert len(result) == 1
    
    @pytest.mark.asyncio
    async def test_reset_session(self, manager):
        manager.retriever._surfaced_this_session.add("test.md")
        manager.retriever._session_bytes = 1000
        
        manager.reset_session()
        
        assert len(manager.retriever._surfaced_this_session) == 0
        assert manager.retriever._session_bytes == 0
    
    def test_get_stats(self, manager):
        stats = manager.get_stats()
        assert stats["enabled"] is True
        assert "memory_count" in stats
        assert "memory_dir" in stats
    
    def test_disabled_stats(self):
        manager = MemoryManager("/tmp/test", enabled=False)
        stats = manager.get_stats()
        assert stats["enabled"] is False


class TestGlobalManager:
    def setup_method(self):
        reset_memory_manager()
    
    def teardown_method(self):
        reset_memory_manager()
    
    def test_get_creates_manager(self):
        manager = get_memory_manager()
        assert manager is not None
        assert isinstance(manager, MemoryManager)
    
    def test_get_returns_same_instance(self):
        m1 = get_memory_manager()
        m2 = get_memory_manager()
        assert m1 is m2
    
    def test_reset_creates_new_instance(self):
        m1 = get_memory_manager()
        reset_memory_manager()
        m2 = get_memory_manager()
        assert m1 is not m2
    
    def test_default_is_global_path(self):
        manager = get_memory_manager()
        home = os.path.expanduser("~")
        assert manager.memory_dir.startswith(home)
        assert ".logicore" in manager.memory_dir
