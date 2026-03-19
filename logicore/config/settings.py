"""
Agentry Unified Configuration
Single source of truth for all deployment and runtime settings.

Priority: Environment Variables > logicore.toml > defaults

Usage:
    from logicore.config.settings import settings
    print(settings.MODE)
    print(settings.OLLAMA_URL)
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Load TOML config if available
_toml_config: Dict[str, Any] = {}
try:
    import tomllib  # Python 3.11+
    _toml_path = Path(__file__).parent.parent.parent / "logicore.toml"
    if _toml_path.exists():
        with open(_toml_path, "rb") as f:
            _toml_config = tomllib.load(f)
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python
        _toml_path = Path(__file__).parent.parent.parent / "logicore.toml"
        if _toml_path.exists():
            with open(_toml_path, "rb") as f:
                _toml_config = tomllib.load(f)
    except ImportError:
        pass  # No TOML support


def _get_toml(section: str, key: str, default=None):
    """Get value from TOML config."""
    if section in _toml_config:
        return _toml_config[section].get(key, default)
    return default


def _get_env(key: str, default: str = None, toml_section: str = None, toml_key: str = None) -> Optional[str]:
    """Get setting with priority: env > toml > default."""
    # First check environment
    env_val = os.getenv(key)
    if env_val is not None:
        return env_val
    
    # Then check TOML
    if toml_section and toml_key:
        toml_val = _get_toml(toml_section, toml_key)
        if toml_val is not None:
            return str(toml_val)
    
    return default


def _get_bool(key: str, default: bool = False, toml_section: str = None, toml_key: str = None) -> bool:
    """Get boolean setting."""
    val = _get_env(key, str(default), toml_section, toml_key)
    if val is None:
        return default
    return str(val).lower() in ("true", "1", "yes", "on")


def _get_int(key: str, default: int, toml_section: str = None, toml_key: str = None) -> int:
    """Get integer setting."""
    val = _get_env(key, str(default), toml_section, toml_key)
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_api_key(provider: str):
    """Get API key for a provider."""
    if provider == "groq":
        return os.getenv("GROQ_API_KEY")
    elif provider == "gemini":
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    elif provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    elif provider == "ollama":
        return os.getenv("OLLAMA_API_KEY")
    elif provider == "azure":
        return os.getenv("AZURE_API_KEY")
    return None


@dataclass
class AgentrySettings:
    """
    Centralized configuration for logicore.
    All settings are loaded from environment variables with sensible defaults.
    """
    
    # ==========================================================================
    # DEPLOYMENT MODE
    # ==========================================================================
    MODE: str = field(default_factory=lambda: _get_env("AGENTRY_MODE", "local", "deployment", "mode"))
    """Deployment mode: 'local' or 'cloud'"""
    
    DEBUG: bool = field(default_factory=lambda: _get_bool("DEBUG", False, "deployment", "debug"))
    """Enable debug logging"""
    
    ENVIRONMENT: str = field(default_factory=lambda: _get_env("ENVIRONMENT", "development", "deployment", "environment"))
    """Environment name: 'development', 'staging', 'production'"""
    
    # ==========================================================================
    # PATHS
    # ==========================================================================
    BASE_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)
    """Project root directory"""
    
    @property
    def UI_DIR(self) -> Path:
        return self.BASE_DIR / "ui"
    
    @property
    def MEDIA_DIR(self) -> Path:
        return self.UI_DIR / "media"
    
    @property
    def DB_PATH(self) -> Path:
        return self.UI_DIR / "scratchy_users.db"
    
    @property
    def LANCEDB_PATH(self) -> str:
        """LanceDB storage path (local or cloud)."""
        # Env > TOML > Default
        path = _get_env("LANCEDB_PATH", None, "embedding", "lancedb_path")
        if path:
            return path
            
        return str(self.BASE_DIR / "logicore" / "user_data" / "lancedb_data")
    
    # ==========================================================================
    # SERVER
    # ==========================================================================
    HOST: str = field(default_factory=lambda: _get_env("HOST", "0.0.0.0", "server", "host"))
    PORT: int = field(default_factory=lambda: _get_int("PORT", 8000, "server", "port"))
    FRONTEND_PORT: int = field(default_factory=lambda: _get_int("FRONTEND_PORT", 3000, "server", "frontend_port"))
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        """Allowed CORS origins."""
        # Try env var first
        origins = _get_env("CORS_ORIGINS")
        if origins:
            return [o.strip() for o in origins.split(",")]
            
        # Try TOML
        toml_origins = _get_toml("server", "cors_origins")
        if toml_origins and isinstance(toml_origins, list):
            return toml_origins
            
        return [
            f"http://localhost:{self.PORT}",
            f"http://localhost:{self.FRONTEND_PORT}",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:3000",
        ]
    
    # ==========================================================================
    # EMBEDDING & SIMPLEMEM
    # ==========================================================================
    EMBEDDING_PROVIDER: str = field(default_factory=lambda: _get_env("EMBEDDING_PROVIDER", "ollama", "embedding", "provider"))
    """Embedding provider: 'ollama' or 'huggingface'"""
    
    EMBEDDING_MODEL: str = field(default_factory=lambda: _get_env("EMBEDDING_MODEL", "qwen3-embedding:0.6b", "embedding", "model"))
    """Embedding model name"""
    
    @property
    def OLLAMA_URL(self) -> str:
        """Ollama API URL (auto-detects Kubernetes)."""
        return _get_env("OLLAMA_URL", "http://localhost:11434", "embedding", "ollama_url")
    
    SIMPLEMEM_ENABLED: bool = field(default_factory=lambda: _get_bool("SIMPLEMEM_ENABLED", True, "simplemem", "enabled"))
    """Enable SimpleMem context engineering"""
    
    SIMPLEMEM_WINDOW_SIZE: int = field(default_factory=lambda: _get_int("SIMPLEMEM_WINDOW_SIZE", 6, "simplemem", "window_size"))
    """Dialogue window size for memory processing"""
    
    SIMPLEMEM_TOP_K: int = field(default_factory=lambda: _get_int("SIMPLEMEM_TOP_K", 5, "simplemem", "top_k"))
    """Number of memories to retrieve"""
    
    # ==========================================================================
    # CLOUD SERVICES (when MODE=cloud)
    # ==========================================================================
    @property
    def SUPABASE_URL(self) -> Optional[str]:
        return _get_env("SUPABASE_URL", None, "cloud", "supabase_url")
    
    @property
    def SUPABASE_KEY(self) -> Optional[str]:
        return _get_env("SUPABASE_KEY", None, "cloud", "supabase_key")
    
    @property
    def BLOB_READ_WRITE_TOKEN(self) -> Optional[str]:
        return _get_env("BLOB_READ_WRITE_TOKEN", None, "cloud", "blob_read_write_token")
    
    # ==========================================================================
    # AGENT DEFAULTS
    # ==========================================================================
    DEFAULT_PROVIDER: str = field(default_factory=lambda: _get_env("DEFAULT_PROVIDER", "ollama", "agent", "default_provider"))
    DEFAULT_MODEL: str = field(default_factory=lambda: _get_env("DEFAULT_MODEL", "gpt-oss:20b-cloud", "agent", "default_model"))
    MAX_ITERATIONS: int = field(default_factory=lambda: _get_int("MAX_ITERATIONS", 40, "agent", "max_iterations"))
    
    # ==========================================================================
    # SMTP (Email)
    # ==========================================================================
    SMTP_HOST: str = field(default_factory=lambda: _get_env("SMTP_HOST", "smtp.gmail.com", "smtp", "host"))
    SMTP_PORT: int = field(default_factory=lambda: _get_int("SMTP_PORT", 587, "smtp", "port"))
    SMTP_USER: str = field(default_factory=lambda: _get_env("SMTP_USER", "", "smtp", "user"))
    SMTP_PASSWORD: str = field(default_factory=lambda: _get_env("SMTP_PASSWORD", "", "smtp", "password"))
    SMTP_FROM_EMAIL: str = field(default_factory=lambda: _get_env("SMTP_FROM_EMAIL", "", "smtp", "from_email"))
    SMTP_USE_TLS: bool = field(default_factory=lambda: _get_bool("SMTP_USE_TLS", True, "smtp", "use_tls"))
    
    # ==========================================================================
    # KUBERNETES / AZURE (for deployment)
    # ==========================================================================
    ACR_REGISTRY: str = field(default_factory=lambda: _get_env("ACR_REGISTRY", "logicoreacr.azurecr.io", "kubernetes", "acr_registry"))
    AKS_CLUSTER: str = field(default_factory=lambda: _get_env("AKS_CLUSTER", "logicore-aks", "kubernetes", "aks_cluster"))
    AKS_RESOURCE_GROUP: str = field(default_factory=lambda: _get_env("AKS_RESOURCE_GROUP", "logicore-rg", "kubernetes", "aks_resource_group"))
    
    # ==========================================================================
    # HELPER METHODS
    # ==========================================================================
    @property
    def is_cloud(self) -> bool:
        """Check if running in cloud mode."""
        return self.MODE.lower() == "cloud"
    
    @property
    def is_local(self) -> bool:
        """Check if running in local mode."""
        return self.MODE.lower() == "local"
    
    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.ENVIRONMENT.lower() == "production"
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if self.is_cloud:
            if not self.SUPABASE_URL:
                errors.append("SUPABASE_URL is required in cloud mode")
            if not self.SUPABASE_KEY:
                errors.append("SUPABASE_KEY is required in cloud mode")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert settings to dictionary (for debugging)."""
        return {
            "MODE": self.MODE,
            "DEBUG": self.DEBUG,
            "ENVIRONMENT": self.ENVIRONMENT,
            "HOST": self.HOST,
            "PORT": self.PORT,
            "OLLAMA_URL": self.OLLAMA_URL,
            "LANCEDB_PATH": self.LANCEDB_PATH,
            "SIMPLEMEM_ENABLED": self.SIMPLEMEM_ENABLED,
            "EMBEDDING_PROVIDER": self.EMBEDDING_PROVIDER,
            "EMBEDDING_MODEL": self.EMBEDDING_MODEL,
            "is_cloud": self.is_cloud,
            "is_production": self.is_production,
        }
    
    def print_config(self):
        """Print configuration summary."""
        print("\n" + "="*60)
        print("  AGENTRY CONFIGURATION")
        print("="*60)
        for key, value in self.to_dict().items():
            print(f"  {key:25} = {value}")
        print("="*60 + "\n")


# Singleton instance
settings = AgentrySettings()

# Backwards compatibility exports
MODE = settings.MODE
DEBUG = settings.DEBUG
OLLAMA_URL = settings.OLLAMA_URL
LANCEDB_PATH = settings.LANCEDB_PATH
