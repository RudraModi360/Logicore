import pytest
import asyncio
import json
import os
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch

from logicore.tools.registry import ToolRegistry, TOOL_PRESETS
from logicore.tools.base import BaseTool, ToolResult
from logicore.tools.datetime import DateTimeTool, DateTimeParams
from logicore.tools.notes import NotesTool, NotesParams
from logicore.agent.base import Agent
from logicore.agent.variants.basic import BasicAgent
from logicore.gateway.gateway import ProviderGateway, get_gateway_for_provider
from logicore.session.manager import SessionManager, SessionStorage
from logicore.config.prompts import get_system_prompt
from logicore.runtime.config import RuntimeConfig
from logicore.providers.availability import ModelAvailabilityService
from logicore.context_engine.token_estimator import TokenEstimator, estimate_tokens
from logicore.mcp.client import MCPClientManager


# ──────────────────────────────────────────────────────────────────────────────
# 1. Tool Registry Full Pipeline
# ──────────────────────────────────────────────────────────────────────────────

class TestToolRegistryFullPipeline:
    def test_registry_creates_with_default_preset(self):
        registry = ToolRegistry()
        assert len(registry.tool_names) > 0

    def test_registry_creates_with_lightweight_preset(self):
        registry = ToolRegistry(preset="lightweight")
        assert len(registry.tool_names) > 0
        assert "read_file" in registry.tool_names
        assert "web_search" in registry.tool_names

    def test_registry_creates_with_smart_preset(self):
        registry = ToolRegistry(preset="smart")
        assert "bash" in registry.tool_names
        assert "datetime" in registry.tool_names
        assert "notes" in registry.tool_names

    def test_tool_lookup_by_name(self):
        registry = ToolRegistry()
        for tool_name in registry.tool_names:
            tool = registry.get_tool(tool_name)
            assert tool is not None, f"Tool '{tool_name}' should be retrievable"
            assert tool.name == tool_name

    def test_has_tool(self):
        registry = ToolRegistry()
        assert registry.has_tool("read_file")
        assert not registry.has_tool("nonexistent_tool_xyz")

    def test_all_schemas_are_valid_json_schema(self):
        registry = ToolRegistry()
        for schema in registry.schemas:
            assert isinstance(schema, dict)
            assert schema.get("type") == "function"
            func = schema.get("function", {})
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            params = func["parameters"]
            assert "type" in params
            assert params["type"] == "object"
            assert "properties" in params

    def test_disabled_tools_are_excluded(self):
        registry = ToolRegistry(disabled_tools=["read_file", "web_search"])
        assert not registry.has_tool("read_file")
        assert not registry.has_tool("web_search")

    def test_enabled_tools_limit_registration(self):
        registry = ToolRegistry(enabled_tools=["datetime", "notes"])
        assert registry.has_tool("datetime")
        assert registry.has_tool("notes")
        assert "read_file" not in registry.tool_names

    def test_execute_tool_unknown_returns_error(self):
        registry = ToolRegistry()
        result = registry.execute_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_presets_are_defined(self):
        assert "lightweight" in TOOL_PRESETS
        assert "smart" in TOOL_PRESETS
        assert "copilot" in TOOL_PRESETS
        assert "full" in TOOL_PRESETS
        assert "minimal" in TOOL_PRESETS


# ──────────────────────────────────────────────────────────────────────────────
# 2. Agent Creation and Tool Loading
# ──────────────────────────────────────────────────────────────────────────────

class TestAgentCreationAndToolLoading:
    def _make_mock_provider(self):
        provider = MagicMock(spec=["provider_name", "model_name"])
        provider.provider_name = "mock"
        provider.model_name = "mock-model"
        return provider

    def test_agent_creates_with_mock_provider(self):
        provider = self._make_mock_provider()
        with patch("logicore.gateway.gateway.get_gateway_for_provider") as mock_gw, \
             patch("logicore.context_engine.token_estimator.get_model_context_window", return_value=128000):
            mock_gw.return_value = MagicMock(spec=ProviderGateway)
            agent = Agent(llm=provider)
            assert agent.provider is provider

    def test_agent_load_default_tools(self):
        provider = self._make_mock_provider()
        with patch("logicore.gateway.gateway.get_gateway_for_provider") as mock_gw, \
             patch("logicore.context_engine.token_estimator.get_model_context_window", return_value=128000):
            mock_gw.return_value = MagicMock(spec=ProviderGateway)
            agent = Agent(llm=provider)
            agent.load_default_tools()
            assert agent.supports_tools is True
            assert len(agent.internal_tools) > 0

    def test_agent_tools_preset(self):
        provider = self._make_mock_provider()
        with patch("logicore.gateway.gateway.get_gateway_for_provider") as mock_gw, \
             patch("logicore.context_engine.token_estimator.get_model_context_window", return_value=128000):
            mock_gw.return_value = MagicMock(spec=ProviderGateway)
            agent = Agent(llm=provider, tool_preset="lightweight")
            assert agent.supports_tools is True
            tool_names = [
                t.get("function", {}).get("name")
                for t in agent.internal_tools
                if isinstance(t, dict)
            ]
            assert "read_file" in tool_names

    def test_agent_disables_tools(self):
        provider = self._make_mock_provider()
        with patch("logicore.gateway.gateway.get_gateway_for_provider") as mock_gw, \
             patch("logicore.context_engine.token_estimator.get_model_context_window", return_value=128000):
            mock_gw.return_value = MagicMock(spec=ProviderGateway)
            agent = Agent(llm=provider)
            agent.disable_tools("testing")
            assert agent.supports_tools is False
            assert len(agent.internal_tools) == 0


# ──────────────────────────────────────────────────────────────────────────────
# 3. Provider Gateway Initialization
# ──────────────────────────────────────────────────────────────────────────────

class TestProviderGatewayInitialization:
    def test_get_gateway_for_mock_provider(self):
        provider = MagicMock()
        provider.provider_name = "openai"
        gateway = get_gateway_for_provider(provider)
        assert gateway is not None
        assert isinstance(gateway, ProviderGateway)

    def test_gateway_stores_provider_reference(self):
        provider = MagicMock(spec=["provider_name", "model_name"])
        provider.provider_name = "openai"
        provider.model_name = "gpt-4"
        gateway = get_gateway_for_provider(provider)
        assert gateway.provider is provider
        assert gateway.model_name == "gpt-4"

    def test_normalized_message_to_dict(self):
        from logicore.gateway.gateway import NormalizedMessage
        msg = NormalizedMessage(role="assistant", content="hello")
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "hello"
        assert "tool_calls" not in d

    def test_normalized_message_with_tool_calls(self):
        from logicore.gateway.gateway import NormalizedMessage
        tool_calls = [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]
        msg = NormalizedMessage(role="assistant", content="", tool_calls=tool_calls)
        d = msg.to_dict()
        assert len(d["tool_calls"]) == 1


# ──────────────────────────────────────────────────────────────────────────────
# 4. Session Manager Create/Retrieve
# ──────────────────────────────────────────────────────────────────────────────

class TestSessionManagerCreateRetrieve:
    def test_session_manager_creates_and_retrieves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_sessions.db")
            storage = SessionStorage(db_path=db_path)
            manager = SessionManager(storage=storage)

            messages = [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
            manager.save_session("test-sess-1", messages, metadata={"title": "Test Session"})

            loaded = manager.load_session("test-sess-1")
            assert len(loaded) == 3
            assert loaded[1]["content"] == "Hello"

    def test_session_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_sessions.db")
            storage = SessionStorage(db_path=db_path)
            manager = SessionManager(storage=storage)

            assert not manager.session_exists("new-session")
            manager.save_session("new-session", [{"role": "user", "content": "test"}])
            assert manager.session_exists("new-session")

    def test_session_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_sessions.db")
            storage = SessionStorage(db_path=db_path)
            manager = SessionManager(storage=storage)

            manager.save_session("del-session", [{"role": "user", "content": "test"}])
            assert manager.session_exists("del-session")
            manager.delete_session("del-session")

    def test_load_empty_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_sessions.db")
            storage = SessionStorage(db_path=db_path)
            manager = SessionManager(storage=storage)

            loaded = manager.load_session("nonexistent")
            assert loaded == []


# ──────────────────────────────────────────────────────────────────────────────
# 5. Prompt Generation for All Roles
# ──────────────────────────────────────────────────────────────────────────────

class TestPromptGenerationForAllRoles:
    def test_general_role_prompt(self):
        prompt = get_system_prompt("gpt-4", role="general")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "gpt-4" in prompt

    def test_engineer_role_prompt(self):
        prompt = get_system_prompt("gpt-4", role="engineer")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "Engineer" in prompt or "engineer" in prompt

    def test_copilot_role_prompt(self):
        prompt = get_system_prompt("gpt-4", role="copilot")
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        assert "Copilot" in prompt or "copilot" in prompt

    def test_smart_role_prompt(self):
        prompt = get_system_prompt("gpt-4", role="smart")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_mcp_role_prompt(self):
        prompt = get_system_prompt("gpt-4", role="mcp")
        assert isinstance(prompt, str)
        assert "MCP" in prompt or "Tool Discovery" in prompt

    def test_prompt_includes_tools_when_provided(self):
        tools = [{"function": {"name": "test_tool", "description": "A test tool", "parameters": {"type": "object", "properties": {}}}}]
        prompt = get_system_prompt("gpt-4", role="general", tools=tools)
        assert "test_tool" in prompt


# ──────────────────────────────────────────────────────────────────────────────
# 6. Tool Execution Pipeline
# ──────────────────────────────────────────────────────────────────────────────

class TestToolExecutionPipeline:
    def test_datetime_tool_now(self):
        tool = DateTimeTool()
        result = tool.run(operation="now")
        assert result["success"] is True
        assert "content" in result
        content = result["content"]
        assert isinstance(content, str)

    def test_datetime_tool_format(self):
        tool = DateTimeTool()
        result = tool.run(operation="format", format_string="%Y-%m-%d")
        assert result["success"] is True
        assert len(result["content"]) == 10  # YYYY-MM-DD

    def test_datetime_tool_schema(self):
        tool = DateTimeTool()
        schema = tool.schema
        assert schema["function"]["name"] == "datetime"
        assert "parameters" in schema["function"]

    def test_notes_tool_add_and_list(self):
        tool = NotesTool()
        add_result = tool.run(action="add", title="Test Note", content="Some content here")
        assert add_result["success"] is True

        list_result = tool.run(action="list")
        assert list_result["success"] is True
        assert "Test Note" in list_result["content"]

        # Cleanup
        tool.run(action="delete", note_id=tool.notes["items"][-1]["id"] if tool.notes["items"] else None)

    def test_notes_tool_search(self):
        tool = NotesTool()
        tool.run(action="add", title="Integration Test", content="Findable content")

        search_result = tool.run(action="search", query="Integration")
        assert search_result["success"] is True
        assert "Integration" in search_result["content"]

        # Cleanup
        for note in list(tool.notes["items"]):
            if note["title"] == "Integration Test":
                tool.run(action="delete", note_id=note["id"])

    def test_registry_execute_tool(self):
        registry = ToolRegistry(preset="smart")
        result = registry.execute_tool("datetime", {"operation": "now"})
        assert result["success"] is True

    def test_registry_execute_with_validation_error(self):
        registry = ToolRegistry(preset="smart")
        result = registry.execute_tool("notes", {"action": "invalid_action"})
        assert result["success"] is False


# ──────────────────────────────────────────────────────────────────────────────
# 7. Runtime Config Defaults
# ──────────────────────────────────────────────────────────────────────────────

class TestRuntimeConfigDefaults:
    def test_runtime_config_creates_with_defaults(self):
        config = RuntimeConfig()
        assert config.max_turns > 0
        assert config.http_timeout_seconds > 0
        assert config.debug is False

    def test_loop_detection_config_defaults(self):
        config = RuntimeConfig()
        ld = config.loop_detection
        assert ld.enabled is True
        assert ld.tool_call_threshold > 0
        assert ld.content_repetition_threshold > 0
        assert ld.llm_check_after_turns > 0

    def test_context_config_defaults(self):
        config = RuntimeConfig()
        ctx = config.context
        assert ctx.max_context_tokens > 0
        assert 0 < ctx.compression_threshold_ratio < 1
        assert ctx.preserve_recent_count > 0
        assert ctx.system_prompt_max_chars > 0

    def test_tool_config_defaults(self):
        config = RuntimeConfig()
        tc = config.tool
        assert tc.max_output_chars > 0
        assert tc.cache_ttl_seconds >= 0
        assert tc.execution_timeout_seconds > 0
        assert tc.enable_deduplication is True

    def test_retry_config_defaults(self):
        config = RuntimeConfig()
        rc = config.retry
        assert rc.max_attempts > 0
        assert rc.base_delay_ms > 0
        assert rc.use_exponential_backoff is True

    def test_telemetry_config_defaults(self):
        config = RuntimeConfig()
        tc = config.telemetry
        assert isinstance(tc.enabled, bool)
        assert tc.log_format in ("json", "text")

    def test_to_dict_roundtrip(self):
        config = RuntimeConfig()
        d = config.to_dict()
        assert isinstance(d, dict)
        assert "max_turns" in d
        assert "context" in d
        assert "tool" in d
        assert "retry" in d
        assert "telemetry" in d


# ──────────────────────────────────────────────────────────────────────────────
# 8. Provider Availability Service
# ──────────────────────────────────────────────────────────────────────────────

class TestProviderAvailabilityService:
    def test_instantiation(self):
        service = ModelAvailabilityService()
        assert service is not None

    def test_register_and_get_provider(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider, priority=1)
        assert service.get_provider("p1") is mock_provider

    def test_get_available_provider_returns_highest_priority(self):
        service = ModelAvailabilityService()
        p_low = MagicMock(name="low_priority")
        p_high = MagicMock(name="high_priority")
        service.register_provider("low", p_low, priority=10)
        service.register_provider("high", p_high, priority=1)
        available = service.get_available_provider()
        assert available is p_high

    def test_report_failure_updates_health(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider)
        service.report_failure("p1", Exception("timeout error"))
        health = service.get_health("p1")
        assert health is not None
        assert health.consecutive_failures >= 1

    def test_report_success_resets_failures(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider)
        service.report_failure("p1", Exception("error"))
        service.report_success("p1")
        health = service.get_health("p1")
        assert health.consecutive_failures == 0

    def test_mark_terminal_makes_unavailable(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider)
        service.mark_terminal("p1", "permanent failure")
        health = service.get_health("p1")
        assert health.is_available is False

    def test_unregister_provider(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider)
        service.unregister_provider("p1")
        assert service.get_provider("p1") is None

    def test_get_stats(self):
        service = ModelAvailabilityService()
        mock_provider = MagicMock()
        service.register_provider("p1", mock_provider)
        stats = service.get_stats()
        assert "p1" in stats
        assert stats["p1"]["state"] == "healthy"


# ──────────────────────────────────────────────────────────────────────────────
# 9. Context Engine / Token Estimator
# ──────────────────────────────────────────────────────────────────────────────

class TestTokenEstimator:
    def test_estimate_tokens_function(self):
        tokens = estimate_tokens("Hello, world!")
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_estimate_empty_string(self):
        tokens = estimate_tokens("")
        assert tokens == 0

    def test_estimator_class(self):
        estimator = TokenEstimator()
        count = estimator.count_tokens("This is a test string for tokens")
        assert count > 0

    def test_estimator_count_message_tokens(self):
        estimator = TokenEstimator()
        msg = {"role": "user", "content": "Hello there"}
        tokens = estimator.count_message_tokens(msg)
        assert tokens > 4  # role overhead + content

    def test_estimator_count_messages_tokens(self):
        estimator = TokenEstimator()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        tokens = estimator.count_messages_tokens(messages)
        assert tokens > 8  # at least 2 * role overhead

    def test_estimator_categorize_tokens(self):
        estimator = TokenEstimator()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
            {"role": "tool", "content": "result"},
        ]
        categories = estimator.categorize_tokens(messages)
        assert "system" in categories
        assert "messages" in categories
        assert "tool_results" in categories
        assert categories["system"] > 0

    def test_estimator_custom_counter(self):
        custom_counter = lambda text: len(text)  # 1 token per char
        estimator = TokenEstimator(token_counter=custom_counter)
        count = estimator.count_tokens("abc")
        assert count == 3


# ──────────────────────────────────────────────────────────────────────────────
# 10. MCP Client Manager
# ──────────────────────────────────────────────────────────────────────────────

class TestMCPClientManager:
    def test_instantiation(self):
        manager = MCPClientManager()
        assert manager is not None
        assert isinstance(manager.sessions, dict)
        assert isinstance(manager.server_tools_map, dict)

    def test_instantiation_with_config(self):
        manager = MCPClientManager(config_path="some_path.json", config={"mcpServers": {}})
        assert manager.config_path == "some_path.json"
        assert manager.config == {"mcpServers": {}}

    def test_has_expected_methods(self):
        manager = MCPClientManager()
        assert hasattr(manager, "connect_to_servers")
        assert hasattr(manager, "get_tools")
        assert hasattr(manager, "execute_tool")
        assert hasattr(manager, "cleanup")

    @pytest.mark.asyncio
    async def test_cleanup_empty_sessions(self):
        manager = MCPClientManager()
        await manager.cleanup()
        assert len(manager.sessions) == 0
        assert len(manager.server_tools_map) == 0

    @pytest.mark.asyncio
    async def test_load_config_from_memory(self):
        config = {"mcpServers": {"test": {"command": "echo"}}}
        manager = MCPClientManager(config=config)
        loaded = await manager.load_config()
        assert loaded == config

    @pytest.mark.asyncio
    async def test_load_config_missing_file(self):
        manager = MCPClientManager(config_path="nonexistent_file_xyz.json")
        loaded = await manager.load_config()
        assert loaded == {}
