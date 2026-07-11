"""
Logicore configuration package.

Importing this package loads the user's ``.env`` exactly once (via
``logicore.config.env``) and exposes the typed settings singleton. No other
module in the framework should read ``os.environ`` directly — import from here.
"""
from .env import (
    load_dotenv,
    _raw,
    _expand,
    resolve,
    storage_root,
)
from .settings import (
    settings,
    AgentrySettings,
    PathSettings,
    get_api_key,
)

# Backwards-compatible alias (deprecated).
LogicoreSettings = AgentrySettings

__all__ = [
    "load_dotenv",
    "settings",
    "AgentrySettings",
    "PathSettings",
    "LogicoreSettings",
    "get_api_key",
]
