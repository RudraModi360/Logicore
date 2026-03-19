__version__ = "1.0.3"

from .agents.agent import Agent
from .agents.copilot import CopilotAgent
from .agents.agent_mcp import MCPAgent
from .agents.agent_smart import SmartAgent, SmartAgentMode
from .agents.agent_basic import BasicAgent, create_agent, tool
from .providers.ollama_provider import OllamaProvider
from .providers.groq_provider import GroqProvider
from .providers.gemini_provider import GeminiProvider
from .providers.azure_provider import AzureProvider
from .providers.base import LLMProvider
from .mcp_client import MCPClientManager
from .session_manager import SessionManager
from .telemetry import TelemetryTracker
from .simplemem import AgentrySimpleMem

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
    "LLMProvider",
    "MCPClientManager",
    "SessionManager",
    "TelemetryTracker",
    "AgentrySimpleMem"
]
