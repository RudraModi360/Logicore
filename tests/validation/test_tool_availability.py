"""
Production Validation: Tool Availability Tests

Verifies that all tools are properly registered and available.
Critical for production - missing tools break agent functionality.
"""

import pytest
from logicore.tools.registry import (
    registry,
    ToolRegistry,
    TOOL_PRESETS,
    SAFE_TOOLS,
    APPROVAL_REQUIRED_TOOLS,
    DANGEROUS_TOOLS,
)
from logicore.tools.base import BaseTool


class TestToolAvailability:
    """Test that all tools are properly registered."""
    
    def test_registry_has_tools(self):
        """Test that the global registry has tools loaded."""
        assert len(registry.tool_names) > 0
    
    def test_registry_tool_count(self):
        """Test that registry has expected number of tools."""
        # Should have at least 30+ tools
        assert len(registry.tool_names) >= 30, f"Expected 30+ tools, got {len(registry.tool_names)}"
    
    def test_core_tools_registered(self):
        """Test that core tools are registered."""
        core_tools = [
            "read_file", "create_file", "edit_file", "list_files",
            "web_search", "execute_command", "code_execute",
        ]
        for tool_name in core_tools:
            assert registry.has_tool(tool_name), f"Core tool '{tool_name}' not registered"
    
    def test_web_tools_registered(self):
        """Test that web tools (Exa) are registered."""
        web_tools = ["web_search", "image_search", "url_fetch"]
        for tool_name in web_tools:
            assert registry.has_tool(tool_name), f"Web tool '{tool_name}' not registered"
    
    def test_all_tools_have_schema(self):
        """Test that all registered tools have valid schemas."""
        for tool_name in registry.tool_names:
            tool = registry.get_tool(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            schema = tool.schema
            assert "type" in schema, f"Tool '{tool_name}' schema missing 'type'"
            assert "function" in schema, f"Tool '{tool_name}' schema missing 'function'"
            assert "name" in schema["function"], f"Tool '{tool_name}' schema missing function name"
            assert schema["function"]["name"] == tool_name, f"Tool '{tool_name}' name mismatch"
    
    def test_all_tools_have_description(self):
        """Test that all tools have descriptions."""
        for tool_name in registry.tool_names:
            tool = registry.get_tool(tool_name)
            assert tool.description, f"Tool '{tool_name}' has no description"
            assert len(tool.description) > 10, f"Tool '{tool_name}' description too short"
    
    def test_preset_registries(self):
        """Test that preset registries are created."""
        from logicore.tools.registry import lightweight_registry, smart_registry, copilot_registry
        assert len(lightweight_registry.tool_names) > 0
        assert len(smart_registry.tool_names) > 0
        assert len(copilot_registry.tool_names) > 0
    
    def test_smart_preset_has_tools(self):
        """Test that smart preset has expected tools."""
        smart_tools = TOOL_PRESETS.get("smart", [])
        assert len(smart_tools) > 20, f"Smart preset should have 20+ tools, got {len(smart_tools)}"
        
        # Check key tools are in smart preset
        key_tools = ["bash", "web_search", "read_file", "create_file"]
        for tool in key_tools:
            assert tool in smart_tools, f"Key tool '{tool}' not in smart preset"
    
    def test_copilot_preset_has_tools(self):
        """Test that copilot preset has expected tools."""
        copilot_tools = TOOL_PRESETS.get("copilot", [])
        assert len(copilot_tools) > 10, f"Copilot preset should have 10+ tools, got {len(copilot_tools)}"
    
    def test_tool_categories_exist(self):
        """Test that tool category lists exist and are non-empty."""
        assert len(SAFE_TOOLS) > 0
        assert len(APPROVAL_REQUIRED_TOOLS) > 0
        assert len(DANGEROUS_TOOLS) > 0
    
    def test_web_search_tool_is_approval_required(self):
        """Test that web_search is in approval required category."""
        assert "web_search" in APPROVAL_REQUIRED_TOOLS
    
    def test_execute_command_is_dangerous(self):
        """Test that execute_command is in dangerous category."""
        assert "execute_command" in DANGEROUS_TOOLS
    
    def test_read_file_is_safe(self):
        """Test that read_file is in safe category."""
        assert "read_file" in SAFE_TOOLS
    
    def test_tool_registry_execute_unknown(self):
        """Test executing unknown tool returns error."""
        result = registry.execute_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]
