"""
Shared pytest fixtures and configuration for Logicore tests.

This file provides common fixtures used across unit, integration, and validation tests.
"""

import pytest
import os
import sys
from unittest.mock import MagicMock, AsyncMock


# Add project root to path for imports
@pytest.fixture(autouse=True)
def add_project_root_to_path():
    """Ensure project root is in sys.path for imports."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLM provider for testing."""
    provider = MagicMock()
    provider.model_name = "test-model"
    provider.chat = AsyncMock(return_value="Test response")
    provider.stream = AsyncMock()
    return provider


@pytest.fixture
def sample_tool_schema():
    """Return a sample tool schema for testing."""
    return {
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "A test tool for validation",
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "The input value"
                    }
                },
                "required": ["input"]
            }
        }
    }


@pytest.fixture
def sample_tool_schemas():
    """Return multiple sample tool schemas for testing."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the internet",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"]
                }
            }
        }
    ]


@pytest.fixture
def temp_directory(tmp_path):
    """Create a temporary directory with test files."""
    # Create test files
    (tmp_path / "test.txt").write_text("Hello, World!")
    (tmp_path / "data.json").write_text('{"key": "value"}')
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("Nested content")
    return tmp_path


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set mock environment variables for testing."""
    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
