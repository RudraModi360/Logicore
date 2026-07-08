"""
Production Validation: Config Health Tests

Verifies that all configuration modules load correctly.
Critical for production - config failures break the entire system.
"""

import pytest
from logicore.config.settings import settings, get_api_key
from logicore.config.prompts import (
    get_system_prompt,
    get_smart_agent_solo_prompt,
    get_mcp_prompt,
    _format_tools,
    _structured_tool_contract,
)
from logicore.runtime.config import RuntimeConfig
from logicore.tools.registry import TOOL_PRESETS, registry


class TestConfigHealth:
    """Test that all configuration loads correctly."""
    
    def test_settings_singleton(self):
        """Test settings singleton loads without error."""
        assert settings is not None
        assert hasattr(settings, 'DEFAULT_PROVIDER')
        assert hasattr(settings, 'DEFAULT_MODEL')
        assert hasattr(settings, 'MAX_ITERATIONS')
    
    def test_settings_defaults(self):
        """Test settings have reasonable defaults."""
        assert settings.MAX_ITERATIONS > 0
        assert settings.DEFAULT_PROVIDER in ["ollama", "groq", "gemini", "openai", "azure"]
    
    def test_runtime_config(self):
        """Test RuntimeConfig loads with defaults."""
        config = RuntimeConfig()
        assert config.max_turns > 0
        assert config.http_timeout_seconds > 0
    
    def test_tool_presets_defined(self):
        """Test that all expected tool presets are defined."""
        expected_presets = ["lightweight", "smart", "copilot", "full", "minimal", "webdev"]
        for preset in expected_presets:
            assert preset in TOOL_PRESETS, f"Preset '{preset}' not found"
    
    def test_tool_preset_contents(self):
        """Test that tool presets contain valid tool names."""
        for preset_name, tools in TOOL_PRESETS.items():
            if preset_name == "full":
                continue  # Special case
            assert isinstance(tools, list), f"Preset '{preset_name}' should be a list"
            assert len(tools) > 0, f"Preset '{preset_name}' should not be empty"
    
    def test_get_system_prompt_general(self):
        """Test general system prompt generation."""
        prompt = get_system_prompt(model_name="test-model", role="general")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "test-model" in prompt
    
    def test_get_system_prompt_engineer(self):
        """Test engineer system prompt generation."""
        prompt = get_system_prompt(model_name="test-model", role="engineer")
        assert isinstance(prompt, str)
        assert "engineer" in prompt.lower() or "software" in prompt.lower()
    
    def test_get_system_prompt_copilot(self):
        """Test copilot system prompt generation."""
        prompt = get_system_prompt(model_name="test-model", role="copilot")
        assert isinstance(prompt, str)
        assert "copilot" in prompt.lower() or "coding" in prompt.lower()
    
    def test_get_smart_agent_solo_prompt(self):
        """Test SmartAgent solo prompt generation."""
        prompt = get_smart_agent_solo_prompt(model_name="test-model")
        assert isinstance(prompt, str)
        assert "SmartAgent" in prompt
    
    def test_get_mcp_prompt(self):
        """Test MCP prompt generation."""
        prompt = get_mcp_prompt(model_name="test-model")
        assert isinstance(prompt, str)
        assert "tool_search_regex" in prompt
    
    def test_format_tools_empty(self):
        """Test _format_tools with empty list."""
        result = _format_tools([])
        assert result == ""
    
    def test_format_tools_with_schemas(self, sample_tool_schemas):
        """Test _format_tools with valid schemas."""
        result = _format_tools(sample_tool_schemas)
        assert isinstance(result, str)
        assert "read_file" in result
        assert "web_search" in result
    
    def test_structured_tool_contract(self):
        """Test structured tool contract generation."""
        contract = _structured_tool_contract()
        assert isinstance(contract, str)
        assert "Tool Calling Contract" in contract
    
    def test_get_api_key_missing(self):
        """Test get_api_key returns None for missing keys."""
        key = get_api_key("nonexistent_provider")
        assert key is None
