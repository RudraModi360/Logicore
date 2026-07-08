"""
Unit Tests: Tool Registry

Tests for tool registration, presets, and execution.
"""

import pytest
from logicore.tools.registry import (
    ToolRegistry,
    TOOL_PRESETS,
    SAFE_TOOLS,
    APPROVAL_REQUIRED_TOOLS,
    DANGEROUS_TOOLS,
    registry,
)
from logicore.tools.base import BaseTool, ToolResult


class TestToolRegistry:
    """Test ToolRegistry functionality."""
    
    def test_registry_creation_default(self):
        """Test creating registry with default (all tools)."""
        reg = ToolRegistry()
        assert len(reg.tool_names) > 0
    
    def test_registry_creation_with_preset(self):
        """Test creating registry with a preset."""
        reg = ToolRegistry(preset="smart")
        assert len(reg.tool_names) > 0
        # Should have fewer tools than full registry
        assert len(reg.tool_names) < len(registry.tool_names)
    
    def test_registry_creation_with_enabled_tools(self):
        """Test creating registry with specific enabled tools."""
        reg = ToolRegistry(enabled_tools=["read_file", "web_search"])
        assert reg.has_tool("read_file")
        assert reg.has_tool("web_search")
        assert not reg.has_tool("create_file")
    
    def test_registry_creation_with_disabled_tools(self):
        """Test creating registry with disabled tools."""
        reg = ToolRegistry(disabled_tools=["web_search"])
        assert not reg.has_tool("web_search")
        assert reg.has_tool("read_file")
    
    def test_register_tool(self):
        """Test registering a custom tool."""
        reg = ToolRegistry(enabled_tools=[])
        
        class CustomTool(BaseTool):
            name = "custom_test_tool"
            description = "A custom test tool"
            
            class args_schema:
                @classmethod
                def model_json_schema(cls):
                    return {"type": "object", "properties": {}}
            
            def run(self, **kwargs):
                return ToolResult(success=True, content="custom")
        
        reg.register_tool(CustomTool())
        assert reg.has_tool("custom_test_tool")
    
    def test_register_duplicate_tool_raises(self):
        """Test registering duplicate tool raises error."""
        reg = ToolRegistry(enabled_tools=["read_file"])
        with pytest.raises(ValueError, match="already registered"):
            reg.register_tool(reg.get_tool("read_file"))
    
    def test_get_tool(self):
        """Test getting a tool by name."""
        tool = registry.get_tool("web_search")
        assert tool is not None
        assert tool.name == "web_search"
    
    def test_get_tool_nonexistent(self):
        """Test getting nonexistent tool returns None."""
        tool = registry.get_tool("nonexistent_tool")
        assert tool is None
    
    def test_tool_schemas(self):
        """Test getting tool schemas."""
        schemas = registry.schemas
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        for schema in schemas:
            assert "type" in schema
            assert "function" in schema
    
    def test_execute_tool(self):
        """Test executing a tool."""
        result = registry.execute_tool("read_file", {"path": "nonexistent.txt"})
        # Should return a ToolResult (even if it fails due to missing file)
        assert isinstance(result, dict)
        assert "success" in result
    
    def test_execute_unknown_tool(self):
        """Test executing unknown tool returns error."""
        result = registry.execute_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]


class TestToolPresets:
    """Test tool preset definitions."""
    
    def test_all_presets_defined(self):
        """Test all expected presets are defined."""
        expected = ["lightweight", "smart", "copilot", "full", "minimal", "webdev"]
        for preset in expected:
            assert preset in TOOL_PRESETS
    
    def test_preset_types(self):
        """Test preset values are correct types."""
        for name, tools in TOOL_PRESETS.items():
            if name == "full":
                assert tools == "__all__"
            else:
                assert isinstance(tools, list)
                assert len(tools) > 0
    
    def test_smart_preset_has_key_tools(self):
        """Test smart preset has essential tools."""
        smart = TOOL_PRESETS["smart"]
        essential = ["bash", "web_search", "read_file", "create_file", "edit_file"]
        for tool in essential:
            assert tool in smart, f"Essential tool '{tool}' missing from smart preset"
    
    def test_copilot_preset_has_coding_tools(self):
        """Test copilot preset has coding-focused tools."""
        copilot = TOOL_PRESETS["copilot"]
        coding = ["read_file", "create_file", "edit_file", "execute_command"]
        for tool in coding:
            assert tool in copilot, f"Coding tool '{tool}' missing from copilot preset"


class TestToolCategories:
    """Test tool category definitions."""
    
    def test_safe_tools_are_read_only(self):
        """Test safe tools are read-only operations."""
        for tool in SAFE_TOOLS:
            assert tool in ["read_file", "list_files", "search_files", "fast_grep", 
                           "read_document", "media_search", "list_cron_jobs", "get_crons",
                           "task_create", "task_get", "task_update", "task_list", "task_next"]
    
    def test_dangerous_tools_are_write_operations(self):
        """Test dangerous tools are write/destructive operations."""
        for tool in DANGEROUS_TOOLS:
            assert tool in ["delete_file", "execute_command", "git_command", "code_execute"]
    
    def test_no_overlap_between_categories(self):
        """Test no tool appears in multiple categories."""
        all_categorized = set(SAFE_TOOLS + APPROVAL_REQUIRED_TOOLS + DANGEROUS_TOOLS)
        assert len(all_categorized) == len(SAFE_TOOLS) + len(APPROVAL_REQUIRED_TOOLS) + len(DANGEROUS_TOOLS)
