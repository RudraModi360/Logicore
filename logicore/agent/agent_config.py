"""
AgentConfig: Typed configuration for Agent construction.

Replaces the 20+ positional parameters in Agent.__init__ with a single
configuration object. Supports both direct construction and dict-based
loading for YAML/JSON config files.

Usage:
    # Direct
    config = AgentConfig(provider="ollama", model="llama3", debug=True)
    agent = Agent(config=config)

    # From dict
    config = AgentConfig.from_dict({"provider": "ollama", "model": "llama3"})
    agent = Agent(config=config)

    # Backward compatible (old positional style still works)
    agent = Agent(provider="ollama", model="llama3")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union


@dataclass
class AgentConfig:
    """Typed configuration for Agent construction.

    All fields have sensible defaults so Agent can be constructed with
    minimal configuration (e.g. ``AgentConfig()`` uses local Ollama).
    """

    # --- Provider ---
    provider: Union[str, Any] = "ollama"
    model: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None

    # --- Agent behavior ---
    system_prompt: Optional[str] = None
    role: str = "general"
    debug: bool = False
    max_iterations: int = 40
    reasoning_level: str = "medium"
    plan_mode: bool = False
    agent_id: Optional[str] = None

    # --- Tools ---
    tools: Optional[List[Any]] = None
    tool_preset: Optional[str] = None
    approval_timeout: float = 120.0
    allow_tools: Optional[Set[str]] = None

    # --- Skills ---
    skills: Optional[List[str]] = None

    # --- Workspace ---
    workspace_root: Optional[str] = None

    # --- Telemetry ---
    telemetry: bool = False

    # --- Storage (optional session persistence) ---
    storage: Optional[Any] = None

    # --- Composable components (optional overrides) ---
    tool_executor: Optional[Any] = None
    chat_orchestrator: Optional[Any] = None
    input_enricher: Optional[Any] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        """Create config from a dictionary (e.g., parsed YAML/JSON).

        Ignores unknown keys gracefully so config files can have extra
        fields without breaking construction.
        """
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize config to a plain dictionary.

        Omits None values and non-serializable component overrides.
        """
        import dataclasses
        result = {}
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            # Skip non-serializable component overrides
            if f.name in ("tool_executor", "chat_orchestrator", "input_enricher", "storage"):
                continue
            # Convert sets to lists for JSON compatibility
            if isinstance(value, set):
                value = sorted(value)
            result[f.name] = value
        return result

    def merge(self, overrides: Dict[str, Any]) -> "AgentConfig":
        """Return a new config with overrides applied.

        Useful for per-session or per-call customization without mutating
        the base config.
        """
        import dataclasses
        current = dataclasses.asdict(self)
        current.update(overrides)
        return AgentConfig.from_dict(current)
