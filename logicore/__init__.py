__version__ = "1.0.3"

from .agent.base import Agent
from .agent.variants.copilot import CopilotAgent
from .agent.variants.mcp import MCPAgent
from .agent.variants.smart import SmartAgent, SmartAgentMode
from .agent.variants.basic import BasicAgent, create_agent, tool
from .providers.ollama_provider import OllamaProvider
from .providers.groq_provider import GroqProvider
from .providers.gemini_provider import GeminiProvider
from .providers.azure_provider import AzureProvider
from .providers.custom_provider import CustomProvider
from .providers.base import LLMProvider
from .mcp.client import MCPClientManager
from .session.manager import SessionManager
from .telemetry.tracker import TelemetryTracker
from .gateway.gateway import ProviderGateway

__all__ = [
    "Agent",
    "CopilotAgent",
    "MCPAgent",
    "SmartAgent",
    "SmartAgentMode",
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
]
