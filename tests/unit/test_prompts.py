"""
Unit Tests: System Prompts

Tests for system prompt generation and formatting.
"""

import pytest
from logicore.config.prompts import (
    get_system_prompt,
    get_smart_agent_solo_prompt,
    get_mcp_prompt,
    _format_tools,
    _get_reasoning_section,
    _get_task_tracking_section,
    _get_plan_mode_section,
    _structured_tool_contract,
    _get_os_specific_bash_guidance,
    _extract_param_type,
)


class TestExtractParamType:
    """Test parameter type extraction."""
    
    def test_extract_string_type(self):
        """Test extracting string type."""
        pinfo = {"type": "string"}
        assert _extract_param_type(pinfo) == "string"
    
    def test_extract_integer_type(self):
        """Test extracting integer type."""
        pinfo = {"type": "integer"}
        assert _extract_param_type(pinfo) == "integer"
    
    def test_extract_anyof_type(self):
        """Test extracting anyOf type."""
        pinfo = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        result = _extract_param_type(pinfo)
        assert "string" in result
    
    def test_extract_empty_returns_string(self):
        """Test that empty/missing type returns string."""
        assert _extract_param_type({}) == "string"
        assert _extract_param_type(None) == "string"


class TestFormatTools:
    """Test tool formatting for prompts."""
    
    def test_format_tools_empty(self):
        """Test formatting empty tools list."""
        result = _format_tools([])
        assert result == ""
    
    def test_format_tools_none(self):
        """Test formatting None tools."""
        result = _format_tools(None)
        assert result == ""
    
    def test_format_tools_with_schema(self, sample_tool_schemas):
        """Test formatting tool schemas."""
        result = _format_tools(sample_tool_schemas)
        assert "read_file" in result
        assert "web_search" in result
        assert "Parameters" in result or "parameters" in result.lower()
    
    def test_format_tools_includes_description(self, sample_tool_schemas):
        """Test that formatting includes tool descriptions."""
        result = _format_tools(sample_tool_schemas)
        assert "Read file contents" in result
        assert "Search the internet" in result
    
    def test_format_tools_includes_parameters(self, sample_tool_schemas):
        """Test that formatting includes parameter info."""
        result = _format_tools(sample_tool_schemas)
        assert "path" in result
        assert "query" in result


class TestReasoningSection:
    """Test reasoning section generation."""
    
    def test_minimal_reasoning(self):
        """Test minimal reasoning level."""
        result = _get_reasoning_section("minimal")
        assert "Quick" in result or "brief" in result.lower()
    
    def test_medium_reasoning(self):
        """Test medium reasoning level."""
        result = _get_reasoning_section("medium")
        assert "Standard" in result or "step-by-step" in result.lower()
    
    def test_deep_reasoning(self):
        """Test deep reasoning level."""
        result = _get_reasoning_section("deep")
        assert "Exhaustive" in result or "deep" in result.lower()
    
    def test_invalid_reasoning_defaults_to_medium(self):
        """Test invalid reasoning level defaults to medium."""
        result = _get_reasoning_section("invalid")
        assert "Standard" in result or "step-by-step" in result.lower()


class TestTaskTrackingSection:
    """Test task tracking section generation."""
    
    def test_task_tracking_includes_steps(self):
        """Test task tracking includes workflow steps."""
        result = _get_task_tracking_section()
        assert "PLAN" in result or "plan" in result.lower()
        assert "task_create" in result
        assert "task_next" in result


class TestPlanModeSection:
    """Test plan mode section generation."""
    
    def test_plan_mode_enabled(self):
        """Test plan mode section when enabled."""
        result = _get_plan_mode_section(enabled=True)
        assert "Plan Mode" in result or "plan_mode" in result.lower()
    
    def test_plan_mode_disabled(self):
        """Test plan mode section when disabled."""
        result = _get_plan_mode_section(enabled=False)
        assert result == ""


class TestStructuredToolContract:
    """Test structured tool contract generation."""
    
    def test_contract_includes_sections(self):
        """Test contract includes required sections."""
        result = _structured_tool_contract()
        assert "Tool Calling Contract" in result
        assert "Tool Result Contract" in result
        assert "Output Style" in result


class TestOsBashGuidance:
    """Test OS-specific bash guidance."""
    
    def test_windows_guidance(self):
        """Test Windows guidance is returned on Windows."""
        result = _get_os_specific_bash_guidance()
        # Should return either Windows or Linux/Mac guidance
        assert isinstance(result, str)
        assert len(result) > 100


class TestSystemPrompts:
    """Test system prompt generation."""
    
    def test_general_prompt(self):
        """Test general agent prompt."""
        prompt = get_system_prompt(model_name="test", role="general")
        assert "test" in prompt
        assert len(prompt) > 500
    
    def test_engineer_prompt(self):
        """Test engineer agent prompt."""
        prompt = get_system_prompt(model_name="test", role="engineer")
        assert "engineer" in prompt.lower() or "software" in prompt.lower()
    
    def test_copilot_prompt(self):
        """Test copilot agent prompt."""
        prompt = get_system_prompt(model_name="test", role="copilot")
        assert "copilot" in prompt.lower() or "coding" in prompt.lower()
    
    def test_smart_agent_solo_prompt(self):
        """Test SmartAgent solo prompt."""
        prompt = get_smart_agent_solo_prompt(model_name="test")
        assert "SmartAgent" in prompt
        assert "web_search" in prompt
    
    def test_mcp_prompt(self):
        """Test MCP agent prompt."""
        prompt = get_mcp_prompt(model_name="test")
        assert "tool_search_regex" in prompt
        assert "MCP" in prompt or "discovery" in prompt.lower()
    
    def test_prompt_with_tools(self, sample_tool_schemas):
        """Test prompt generation with tools."""
        prompt = get_system_prompt(
            model_name="test",
            role="general",
            tools=sample_tool_schemas
        )
        assert "read_file" in prompt
        assert "web_search" in prompt
