__version__ = "1.0.3"

from .agent.base import Agent
from .agent.tool_executor import ToolExecutor
from .agent.chat_orchestrator import ChatOrchestrator
from .agent.input_enricher import InputEnricher
from .agent.variants.copilot import CopilotAgent
from .agent.variants.mcp import MCPAgent
from .agent.variants.smart import SmartAgent
from .agent.variants.basic import BasicAgent, create_agent, tool
from .providers.ollama_provider import OllamaProvider
from .providers.groq_provider import GroqProvider
from .providers.gemini_provider import GeminiProvider
from .providers.azure_provider import AzureProvider
from .providers.custom_provider import CustomProvider
from .providers.base import LLMProvider
from .providers.factory import create_provider, register_provider
from .mcp.client import MCPClientManager
from .session.manager import SessionManager
from .telemetry.tracker import TelemetryTracker
from .gateway.gateway import ProviderGateway
from .skills import Skill, SkillMetadata, SkillLoader, SkillIndexEntry
from .stream.events import StreamEvent, StreamEventType
from .stream.emitter import StreamEmitter
from .stream.result import AgentRunResult
from .stream.sse import as_sse, events_to_sse, SSE_DONE

__all__ = [
    # Core Agent
    "Agent",
    "ToolExecutor",
    "ChatOrchestrator",
    "InputEnricher",
    "CopilotAgent",
    "MCPAgent",
    "SmartAgent",
    "BasicAgent",
    "create_agent",
    "tool",
    # Providers
    "OllamaProvider",
    "GroqProvider",
    "GeminiProvider",
    "AzureProvider",
    "CustomProvider",
    "LLMProvider",
    "create_provider",
    "register_provider",
    # Infrastructure
    "MCPClientManager",
    "SessionManager",
    "TelemetryTracker",
    "ProviderGateway",
    # Skills
    "Skill",
    "SkillMetadata",
    "SkillLoader",
    "SkillIndexEntry",
    # Streaming
    "StreamEvent",
    "StreamEventType",
    "StreamEmitter",
    "AgentRunResult",
    "as_sse",
    "events_to_sse",
    "SSE_DONE",
]
