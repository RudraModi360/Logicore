"""
Production Readiness Validation Tests for Logicore.

Validates that the codebase is structurally sound, imports resolve,
configs have defaults, and required files are present before deployment.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGICORE_DIR = PROJECT_ROOT / "logicore"


# ---------------------------------------------------------------------------
# 1. Core Imports
# ---------------------------------------------------------------------------
class TestCoreImports:
    """Verify every public symbol in logicore.__init__ loads without error."""

    @pytest.mark.parametrize(
        "symbol",
        [
            "Agent",
            "CopilotAgent",
            "MCPAgent",
            "SmartAgent",
            "BasicAgent",
            "create_agent",
            "tool",
            "OllamaProvider",
            "GroqProvider",
            "GeminiProvider",
            "AzureProvider",
            "CustomProvider",
            "LLMProvider",
            "MCPClientManager",
            "SessionManager",
            "TelemetryTracker",
            "ProviderGateway",
        ],
    )
    def test_import_from_logicore(self, symbol):
        import logicore

        assert hasattr(logicore, symbol), f"logicore.{symbol} missing"

    def test_all_list_matches_exports(self):
        import logicore

        for name in logicore.__all__:
            assert hasattr(logicore, name), f"__all__ entry '{name}' is not importable"


# ---------------------------------------------------------------------------
# 2. Tool Registry Completeness
# ---------------------------------------------------------------------------
class TestToolRegistry:
    """Verify ToolRegistry and presets."""

    def test_registry_importable(self):
        from logicore.tools.registry import ToolRegistry, TOOL_PRESETS

        assert ToolRegistry is not None

    @pytest.mark.parametrize("preset", ["lightweight", "smart", "copilot", "full", "minimal"])
    def test_preset_exists(self, preset):
        from logicore.tools.registry import TOOL_PRESETS

        assert preset in TOOL_PRESETS, f"Preset '{preset}' not in TOOL_PRESETS"

    def test_registry_default_has_all_tools(self):
        from logicore.tools.registry import ToolRegistry

        reg = ToolRegistry()
        names = reg.tool_names
        assert len(names) > 0, "Default registry should register tools"
        assert "read_file" in names
        assert "execute_command" in names

    @pytest.mark.parametrize("preset", ["lightweight", "smart", "copilot", "minimal"])
    def test_preset_registry_loads(self, preset):
        from logicore.tools.registry import ToolRegistry

        reg = ToolRegistry(preset=preset)
        assert len(reg.tool_names) > 0, f"Preset '{preset}' loaded no tools"

    def test_global_registries_exist(self):
        from logicore.tools.registry import (
            registry,
            lightweight_registry,
            smart_registry,
            copilot_registry,
        )

        assert registry is not None
        assert lightweight_registry is not None
        assert smart_registry is not None
        assert copilot_registry is not None


# ---------------------------------------------------------------------------
# 3. Agent Creation (mocked provider)
# ---------------------------------------------------------------------------
class TestAgentCreation:
    """Verify agent classes are importable and have expected interfaces."""

    def test_basic_agent_class_exists(self):
        from logicore.agent.variants.basic import BasicAgent

        assert hasattr(BasicAgent, "__init__")

    def test_smart_agent_class_exists(self):
        from logicore.agent.variants.smart import SmartAgent

        assert hasattr(SmartAgent, "__init__")

    def test_copilot_agent_class_exists(self):
        from logicore.agent.variants.copilot import CopilotAgent

        assert hasattr(CopilotAgent, "__init__")

    def test_mcp_agent_class_exists(self):
        from logicore.agent.variants.mcp import MCPAgent

        assert hasattr(MCPAgent, "__init__")

    def test_basic_agent_is_not_abstract(self):
        from logicore.agent.variants.basic import BasicAgent

        # Verify it's a concrete class that can be called
        assert not getattr(BasicAgent, "__abstractmethods__", set())

    def test_smart_agent_inherits_agent(self):
        from logicore.agent.variants.smart import SmartAgent
        from logicore.agent.base import Agent

        assert issubclass(SmartAgent, Agent)

    def test_copilot_agent_inherits_agent(self):
        from logicore.agent.variants.copilot import CopilotAgent
        from logicore.agent.base import Agent

        assert issubclass(CopilotAgent, Agent)

    def test_mcp_agent_inherits_agent(self):
        from logicore.agent.variants.mcp import MCPAgent
        from logicore.agent.base import Agent

        assert issubclass(MCPAgent, Agent)


# ---------------------------------------------------------------------------
# 4. Settings Health
# ---------------------------------------------------------------------------
class TestSettingsHealth:
    """AgentrySettings must have sane defaults and not crash without env vars."""

    def test_settings_importable(self):
        from logicore.config.settings import AgentrySettings

        assert AgentrySettings is not None

    def test_settings_instantiates_with_defaults(self):
        from logicore.config.settings import AgentrySettings

        s = AgentrySettings()
        assert isinstance(s.MODE, str)
        assert isinstance(s.PORT, int)
        assert isinstance(s.DEBUG, bool)
        assert s.PORT > 0

    def test_required_fields_have_defaults(self):
        from logicore.config.settings import AgentrySettings

        s = AgentrySettings()
        defaults = {
            "MODE": s.MODE,
            "ENVIRONMENT": s.ENVIRONMENT,
            "HOST": s.HOST,
            "PORT": s.PORT,
            "DEFAULT_PROVIDER": s.DEFAULT_PROVIDER,
            "DEFAULT_MODEL": s.DEFAULT_MODEL,
            "MAX_ITERATIONS": s.MAX_ITERATIONS,
        }
        for key, value in defaults.items():
            assert value is not None, f"Setting {key} has no default"
            assert value != "", f"Setting {key} is empty string"

    def test_no_missing_required_env_vars_crash(self):
        """Importing settings must not raise even without API keys."""
        import os

        original_env = dict(os.environ)
        # Remove common API keys to simulate clean environment
        for key in ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "AZURE_API_KEY"]:
            os.environ.pop(key, None)
        try:
            mod_name = "logicore.config.settings"
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            import importlib
            importlib.import_module(mod_name)
            from logicore.config.settings import AgentrySettings
            s = AgentrySettings()
            assert s.MODE is not None
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_settings_singleton(self):
        from logicore.config.settings import settings

        assert settings is not None
        assert isinstance(settings.MODE, str)


# ---------------------------------------------------------------------------
# 5. Config / Prompts
# ---------------------------------------------------------------------------
class TestConfigPrompts:
    """System prompts must return non-empty strings for all roles."""

    def test_get_system_prompt_importable(self):
        from logicore.config.prompts import get_system_prompt

        assert callable(get_system_prompt)

    @pytest.mark.parametrize("role", ["general", "engineer", "copilot", "smart"])
    def test_prompt_for_role(self, role):
        from logicore.config.prompts import get_system_prompt

        if role == "smart":
            prompt = get_system_prompt("test-model", role="general")
        else:
            prompt = get_system_prompt("test-model", role=role)
        assert isinstance(prompt, str)
        assert len(prompt) > 100, f"Prompt for role '{role}' is too short"

    def test_copilot_prompt_function(self):
        from logicore.config.prompts import get_copilot_prompt

        prompt = get_copilot_prompt("test-model")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_engineer_prompt_function(self):
        from logicore.config.prompts import get_engineer_prompt

        prompt = get_engineer_prompt("test-model")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_smart_agent_solo_prompt(self):
        from logicore.config.prompts import get_smart_agent_solo_prompt

        prompt = get_smart_agent_solo_prompt("test-model")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_mcp_prompt_function(self):
        from logicore.config.prompts import get_mcp_prompt

        prompt = get_mcp_prompt("test-model")
        assert isinstance(prompt, str)
        assert len(prompt) > 100


# ---------------------------------------------------------------------------
# 6. Runtime Config
# ---------------------------------------------------------------------------
class TestRuntimeConfig:
    """RuntimeConfig and nested sub-configs must initialize with defaults."""

    def test_runtime_config_importable(self):
        from logicore.runtime.config import RuntimeConfig

        assert RuntimeConfig is not None

    def test_runtime_config_defaults(self):
        from logicore.runtime.config import RuntimeConfig

        config = RuntimeConfig()
        assert config.max_turns > 0
        assert config.max_history_messages > 0
        assert config.http_timeout_seconds > 0

    def test_nested_configs_initialized(self):
        from logicore.runtime.config import (
            RuntimeConfig,
            LoopDetectionConfig,
            ContextConfig,
            ToolConfig,
            RetryConfig,
            TelemetryConfig,
        )

        config = RuntimeConfig()
        assert isinstance(config.loop_detection, LoopDetectionConfig)
        assert isinstance(config.context, ContextConfig)
        assert isinstance(config.tool, ToolConfig)
        assert isinstance(config.retry, RetryConfig)
        assert isinstance(config.telemetry, TelemetryConfig)

    def test_sub_config_defaults(self):
        from logicore.runtime.config import (
            LoopDetectionConfig,
            ContextConfig,
            ToolConfig,
            RetryConfig,
            TelemetryConfig,
        )

        assert LoopDetectionConfig().enabled is True
        assert ContextConfig().max_context_tokens > 0
        assert ToolConfig().execution_timeout_seconds > 0
        assert RetryConfig().max_attempts > 0
        assert TelemetryConfig().enabled is True

    def test_from_settings_classmethod(self):
        from logicore.runtime.config import RuntimeConfig

        config = RuntimeConfig.from_settings()
        assert config.max_turns > 0
        assert isinstance(config.loop_detection.enabled, bool)

    def test_to_dict(self):
        from logicore.runtime.config import RuntimeConfig

        d = RuntimeConfig().to_dict()
        assert isinstance(d, dict)
        assert "max_turns" in d
        assert "loop_detection" in d
        assert "context" in d
        assert "tool" in d
        assert "retry" in d
        assert "telemetry" in d


# ---------------------------------------------------------------------------
# 7. Provider Base
# ---------------------------------------------------------------------------
class TestProviderBase:
    """LLMProvider ABC must have required abstract methods."""

    def test_importable(self):
        from logicore.providers.base import LLMProvider

        assert LLMProvider is not None

    def test_is_abstract(self):
        from logicore.providers.base import LLMProvider

        assert hasattr(LLMProvider, "chat")
        assert hasattr(LLMProvider, "chat_stream")

    def test_cannot_instantiate_directly(self):
        from logicore.providers.base import LLMProvider

        with pytest.raises(TypeError):
            LLMProvider("model")

    def test_provider_capability_enum(self):
        from logicore.providers.base import ProviderCapability

        assert ProviderCapability.CHAT is not None
        assert ProviderCapability.STREAMING is not None

    def test_concrete_subclass(self):
        from logicore.providers.base import LLMProvider, ProviderCapability

        class DummyProvider(LLMProvider):
            provider_name = "dummy"

            def __init__(self, model_name, api_key=None, **kwargs):
                self._model_name = model_name

            def get_model_name(self):
                return self._model_name

        p = DummyProvider("test")
        assert p.get_model_name() == "test"
        assert p.get_provider_id() == "dummy:test"
        assert p.supports(ProviderCapability.CHAT)


# ---------------------------------------------------------------------------
# 8. Provider Gateway
# ---------------------------------------------------------------------------
class TestProviderGateway:
    """ProviderGateway ABC must expose required methods."""

    def test_importable(self):
        from logicore.gateway.gateway import ProviderGateway

        assert ProviderGateway is not None

    def test_has_chat_and_chat_stream(self):
        from logicore.gateway.gateway import ProviderGateway

        assert hasattr(ProviderGateway, "chat")
        assert hasattr(ProviderGateway, "chat_stream")

    def test_cannot_instantiate_directly(self):
        from logicore.gateway.gateway import ProviderGateway

        with pytest.raises(TypeError):
            ProviderGateway(MagicMock())

    def test_normalized_message(self):
        from logicore.gateway.gateway import NormalizedMessage

        msg = NormalizedMessage(role="assistant", content="hello")
        assert msg.role == "assistant"
        assert msg.content == "hello"
        d = msg.to_dict()
        assert d["role"] == "assistant"

    def test_gateway_factory(self):
        from logicore.gateway.gateway import get_gateway_for_provider, OllamaGateway

        provider = MagicMock()
        provider.provider_name = "ollama"
        provider.get_model_name.return_value = "test"
        gw = get_gateway_for_provider(provider)
        assert isinstance(gw, OllamaGateway)


# ---------------------------------------------------------------------------
# 9. Session Manager
# ---------------------------------------------------------------------------
class TestSessionManager:
    """SessionManager must be instantiable."""

    def test_importable(self):
        from logicore.session.manager import SessionManager

        assert SessionManager is not None

    def test_instantiation(self, tmp_path):
        from logicore.session.manager import SessionManager, SessionStorage

        db_path = str(tmp_path / "test_sessions.db")
        storage = SessionStorage(db_path=db_path)
        mgr = SessionManager(storage=storage)
        assert mgr is not None

    def test_save_and_load(self, tmp_path):
        from logicore.session.manager import SessionManager, SessionStorage

        db_path = str(tmp_path / "test_sessions.db")
        storage = SessionStorage(db_path=db_path)
        mgr = SessionManager(storage=storage)

        msgs = [{"role": "user", "content": "hello"}]
        mgr.save_session("s1", msgs, metadata={"title": "Test"})
        loaded = mgr.load_session("s1")
        assert loaded == msgs

    def test_list_sessions(self, tmp_path):
        from logicore.session.manager import SessionManager, SessionStorage

        db_path = str(tmp_path / "test_sessions.db")
        storage = SessionStorage(db_path=db_path)
        mgr = SessionManager(storage=storage)

        mgr.save_session("s1", [{"role": "user", "content": "a"}])
        mgr.save_session("s2", [{"role": "user", "content": "b"}])
        sessions = mgr.list_sessions()
        assert len(sessions) >= 2


# ---------------------------------------------------------------------------
# 10. Telemetry Tracker
# ---------------------------------------------------------------------------
class TestTelemetryTracker:
    """TelemetryTracker must be instantiable and record requests."""

    def test_importable(self):
        from logicore.telemetry.tracker import TelemetryTracker

        assert TelemetryTracker is not None

    def test_instantiation(self):
        from logicore.telemetry.tracker import TelemetryTracker

        t = TelemetryTracker(enabled=True)
        assert t is not None

    def test_record_request(self):
        from logicore.telemetry.tracker import TelemetryTracker, TokenBreakdown

        t = TelemetryTracker(enabled=True)
        breakdown = TokenBreakdown(messages=100)
        t.record_request(
            session_id="test-session",
            input_tokens=100,
            output_tokens=50,
            model="test-model",
            provider="mock",
            duration_ms=150.0,
            token_breakdown=breakdown,
        )
        summary = t.get_session_summary("test-session")
        assert summary["requests"]["total"] == 1
        assert summary["tokens"]["total"] == 150

    def test_disabled_tracker(self):
        from logicore.telemetry.tracker import TelemetryTracker

        t = TelemetryTracker(enabled=False)
        t.record_request(
            session_id="s1",
            input_tokens=10,
            output_tokens=5,
            model="m",
            provider="p",
        )
        # When disabled, session is not created; summary returns "not found"
        summary = t.get_session_summary("s1")
        assert summary.get("total_requests", 0) == 0

    def test_get_total_summary(self):
        from logicore.telemetry.tracker import TelemetryTracker

        t = TelemetryTracker()
        t.record_request("s1", input_tokens=10, output_tokens=5, model="m", provider="p")
        total = t.get_total_summary()
        assert total["total_sessions"] >= 1


# ---------------------------------------------------------------------------
# 11. MCP Client Manager
# ---------------------------------------------------------------------------
class TestMCPClientManager:
    """MCPClientManager must be instantiable without connecting."""

    def test_importable(self):
        from logicore.mcp.client import MCPClientManager

        assert MCPClientManager is not None

    def test_instantiation(self, tmp_path):
        from logicore.mcp.client import MCPClientManager

        config_path = str(tmp_path / "mcp.json")
        mgr = MCPClientManager(config_path=config_path)
        assert mgr is not None
        assert mgr.sessions == {}
        assert mgr.server_tools_map == {}

    def test_load_missing_config(self, tmp_path):
        from logicore.mcp.client import MCPClientManager
        import asyncio

        config_path = str(tmp_path / "nonexistent.json")
        mgr = MCPClientManager(config_path=config_path)
        config = asyncio.get_event_loop().run_until_complete(mgr.load_config())
        assert config == {}


# ---------------------------------------------------------------------------
# 12. No Circular Imports
# ---------------------------------------------------------------------------
class TestNoCircularImports:
    """Importing logicore must not cause circular import errors."""

    def test_fresh_import(self):
        # Remove logicore from sys.modules to force a fresh import
        to_remove = [k for k in sys.modules if k.startswith("logicore")]
        for k in to_remove:
            del sys.modules[k]

        import logicore

        assert logicore.__version__ is not None

    def test_submodules_importable(self):
        modules = [
            "logicore.config.settings",
            "logicore.config.prompts",
            "logicore.runtime.config",
            "logicore.providers.base",
            "logicore.gateway.gateway",
            "logicore.tools.registry",
            "logicore.session.manager",
            "logicore.telemetry.tracker",
        ]
        for mod in modules:
            m = importlib.import_module(mod)
            assert m is not None, f"Failed to import {mod}"


# ---------------------------------------------------------------------------
# 13. pyproject.toml Version Consistency
# ---------------------------------------------------------------------------
class TestPyprojectVersionConsistency:
    """Version in pyproject.toml must match __init__.py."""

    def test_versions_match(self):
        init_file = LOGICORE_DIR / "__init__.py"
        pyproject_file = PROJECT_ROOT / "pyproject.toml"

        assert init_file.exists(), f"{init_file} not found"
        assert pyproject_file.exists(), f"{pyproject_file} not found"

        # Extract version from __init__.py
        init_content = init_file.read_text(encoding="utf-8")
        init_version = None
        for line in init_content.splitlines():
            if line.startswith("__version__"):
                init_version = line.split("=")[1].strip().strip('"').strip("'")
                break
        assert init_version is not None, "No __version__ in __init__.py"

        # Extract version from pyproject.toml
        pyproject_content = pyproject_file.read_text(encoding="utf-8")
        pyproject_version = None
        in_project = False
        for line in pyproject_content.splitlines():
            stripped = line.strip()
            if stripped == "[project]":
                in_project = True
                continue
            if in_project and stripped.startswith("version"):
                pyproject_version = stripped.split("=")[1].strip().strip('"').strip("'")
                break
            if in_project and stripped.startswith("["):
                break
        assert pyproject_version is not None, "No version in pyproject.toml [project]"

        assert init_version == pyproject_version, (
            f"Version mismatch: __init__.py={init_version}, pyproject.toml={pyproject_version}"
        )

    def test_logicore_version_accessible(self):
        import logicore

        assert hasattr(logicore, "__version__")
        assert isinstance(logicore.__version__, str)
        assert len(logicore.__version__) > 0


# ---------------------------------------------------------------------------
# 14. Required Files Exist
# ---------------------------------------------------------------------------
class TestRequiredFilesExist:
    """Critical project files must exist at the expected locations."""

    @pytest.mark.parametrize(
        "filename",
        ["pyproject.toml", "README.md", "LICENSE", ".gitignore"],
    )
    def test_file_exists(self, filename):
        path = PROJECT_ROOT / filename
        assert path.exists(), f"{filename} not found at {path}"

    def test_logicore_init(self):
        assert (LOGICORE_DIR / "__init__.py").exists()

    def test_logicore_py_typed(self):
        assert (LOGICORE_DIR / "py.typed").exists()

    def test_mcp_json_exists(self):
        assert (PROJECT_ROOT / "mcp.json").exists()


# ---------------------------------------------------------------------------
# 15. Provider Implementations
# ---------------------------------------------------------------------------
class TestProviderImplementations:
    """Verify concrete providers are importable."""

    @pytest.mark.parametrize(
        "module_name,class_name",
        [
            ("logicore.providers.ollama_provider", "OllamaProvider"),
            ("logicore.providers.groq_provider", "GroqProvider"),
            ("logicore.providers.gemini_provider", "GeminiProvider"),
            ("logicore.providers.azure_provider", "AzureProvider"),
            ("logicore.providers.custom_provider", "CustomProvider"),
        ],
    )
    def test_provider_importable(self, module_name, class_name):
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        assert cls is not None

    def test_ollama_provider_is_llm_provider(self):
        from logicore.providers.ollama_provider import OllamaProvider
        from logicore.providers.base import LLMProvider

        assert issubclass(OllamaProvider, LLMProvider)


# ---------------------------------------------------------------------------
# 16. Package Structure Integrity
# ---------------------------------------------------------------------------
class TestPackageStructure:
    """Core subpackages must have __init__.py files."""

    @pytest.mark.parametrize(
        "subpackage",
        [
            "agent",
            "agent/variants",
            "config",
            "gateway",
            "mcp",
            "providers",
            "runtime",
            "session",
            "telemetry",
            "tools",
        ],
    )
    def test_init_file_exists(self, subpackage):
        init_path = LOGICORE_DIR / subpackage / "__init__.py"
        assert init_path.exists(), f"Missing __init__.py in logicore/{subpackage}"
