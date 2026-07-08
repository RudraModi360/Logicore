"""
Production Validation: Agent Creation Tests

Verifies that all agent variants can be imported and have expected attributes.
Critical for production - agent creation failures break the user experience.

Note: Full instantiation tests require a running LLM provider (Ollama, etc.)
These tests verify the classes are properly defined and importable.
"""

import pytest
from logicore.agent.variants.basic import BasicAgent
from logicore.agent.variants.smart import SmartAgent
from logicore.agent.variants.copilot import CopilotAgent
from logicore.agent.variants.mcp import MCPAgent


class TestAgentCreation:
    """Test that all agent variants are properly defined."""
    
    def test_basic_agent_class_exists(self):
        """Test BasicAgent class is importable."""
        assert BasicAgent is not None
        assert hasattr(BasicAgent, 'chat')
        assert hasattr(BasicAgent, 'add_tool')
    
    def test_smart_agent_class_exists(self):
        """Test SmartAgent class is importable."""
        assert SmartAgent is not None
        assert hasattr(SmartAgent, 'chat')
        assert hasattr(SmartAgent, 'reason')
    
    def test_copilot_agent_class_exists(self):
        """Test CopilotAgent class is importable."""
        assert CopilotAgent is not None
        assert hasattr(CopilotAgent, 'chat')
    
    def test_mcp_agent_class_exists(self):
        """Test MCPAgent class is importable."""
        assert MCPAgent is not None
        assert hasattr(MCPAgent, 'chat')
    
    def test_basic_agent_has_expected_methods(self):
        """Test BasicAgent has expected public methods."""
        expected_methods = ['chat', 'chat_sync', 'add_tool', 'add_tools', 
                          'set_callbacks', 'clear_history', 'get_session']
        for method in expected_methods:
            assert hasattr(BasicAgent, method), f"BasicAgent missing method: {method}"
    
    def test_smart_agent_has_expected_methods(self):
        """Test SmartAgent has expected public methods."""
        expected_methods = ['chat', 'reason', 'status']
        for method in expected_methods:
            assert hasattr(SmartAgent, method), f"SmartAgent missing method: {method}"
