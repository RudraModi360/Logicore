"""
Production Validation: Import Health Tests

Verifies that all Logicore modules can be imported without errors.
This is critical for production deployment - if imports fail, the package is broken.
"""

import pytest
import importlib


class TestImportHealth:
    """Test that all core modules import successfully."""
    
    def test_import_logicore_core(self):
        """Test core logicore package imports."""
        import logicore
        assert hasattr(logicore, '__version__')
    
    def test_import_agent_base(self):
        """Test agent base module imports."""
        from logicore.agent.base import Agent
        assert Agent is not None
    
    def test_import_basic_agent(self):
        """Test BasicAgent imports."""
        from logicore.agent.variants.basic import BasicAgent
        assert BasicAgent is not None
    
    def test_import_smart_agent(self):
        """Test SmartAgent imports."""
        from logicore.agent.variants.smart import SmartAgent
        assert SmartAgent is not None
    
    def test_import_copilot_agent(self):
        """Test CopilotAgent imports."""
        from logicore.agent.variants.copilot import CopilotAgent
        assert CopilotAgent is not None
    
    def test_import_mcp_agent(self):
        """Test MCPAgent imports."""
        from logicore.agent.variants.mcp import MCPAgent
        assert MCPAgent is not None
    
    def test_import_tools_base(self):
        """Test tools base module imports."""
        from logicore.tools.base import BaseTool, ToolResult
        assert BaseTool is not None
        assert ToolResult is not None
    
    def test_import_tool_registry(self):
        """Test tool registry imports."""
        from logicore.tools.registry import ToolRegistry, registry
        assert ToolRegistry is not None
        assert registry is not None
    
    def test_import_web_tools(self):
        """Test web tools import (Exa API)."""
        from logicore.tools.web import WebSearchTool, UrlFetchTool, ImageSearchTool
        assert WebSearchTool is not None
        assert UrlFetchTool is not None
        assert ImageSearchTool is not None
    
    def test_import_filesystem_tools(self):
        """Test filesystem tools import."""
        from logicore.tools.filesystem import (
            ReadFileTool, CreateFileTool, EditFileTool, DeleteFileTool,
            ListFilesTool, SearchFilesTool, FastGrepTool
        )
        assert ReadFileTool is not None
    
    def test_import_execution_tools(self):
        """Test execution tools import."""
        from logicore.tools.execution import ExecuteCommandTool, CodeExecuteTool
        assert ExecuteCommandTool is not None
        assert CodeExecuteTool is not None
    
    def test_import_config_prompts(self):
        """Test config prompts module imports."""
        from logicore.config.prompts import get_system_prompt, _format_tools
        assert get_system_prompt is not None
        assert _format_tools is not None
    
    def test_import_config_settings(self):
        """Test config settings module imports."""
        from logicore.config.settings import settings, get_api_key
        assert settings is not None
        assert get_api_key is not None
    
    def test_import_providers(self):
        """Test provider modules import."""
        from logicore.providers.base import LLMProvider
        assert LLMProvider is not None
    
    def test_import_runtime_config(self):
        """Test runtime config imports."""
        from logicore.runtime.config import RuntimeConfig
        assert RuntimeConfig is not None
    
    @pytest.mark.parametrize("module_path", [
        "logicore.tools.git",
        "logicore.tools.document",
        "logicore.tools.pdf",
        "logicore.tools.notes",
        "logicore.tools.datetime",
        "logicore.tools.think",
        "logicore.tools.bash",
        "logicore.tools.media",
        "logicore.tools.cron",
        "logicore.tools.plan",
    ])
    def test_import_tool_modules(self, module_path):
        """Test that all tool modules import successfully."""
        module = importlib.import_module(module_path)
        assert module is not None
