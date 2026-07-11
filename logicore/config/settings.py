"""
Logicore Unified Configuration
Single source of truth for all deployment and runtime settings.

Priority: Environment Variables (.env) > defaults

Usage:
    from logicore.config.settings import settings
    print(settings.MODE)
    print(settings.OLLAMA_URL)
"""
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

# All environment access is owned by logicore.config.env. This module only
# reads configuration through that single gateway (never os.environ directly).
from .env import _raw, _expand, resolve, storage_root


def _get_env(key: str, default: str = None, toml_section: str = None, toml_key: str = None) -> Optional[str]:
    """Get a string setting from the environment (TOML support removed)."""
    return _raw(key, default)


def _get_bool(key: str, default: bool = False, toml_section: str = None, toml_key: str = None) -> bool:
    """Get a boolean setting."""
    val = _raw(key)
    if val is None:
        val = str(default)
    return str(val).lower() in ("true", "1", "yes", "on")


def _get_int(key: str, default: int, toml_section: str = None, toml_key: str = None) -> int:
    """Get an integer setting."""
    val = _raw(key)
    if val is None:
        val = str(default)
    try:
        return int(val)
    except (ValueError, TypeError):
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
    """Get API key for a provider (provider-native env names)."""
    if provider == "groq":
        return _raw("GROQ_API_KEY")
    elif provider == "gemini":
        return _raw("GEMINI_API_KEY") or _raw("GOOGLE_API_KEY")
    elif provider == "openai":
        return _raw("OPENAI_API_KEY")
    elif provider == "ollama":
        return _raw("OLLAMA_API_KEY")
    elif provider == "azure":
        return _raw("AZURE_API_KEY")
    elif provider == "exa":
        return _raw("EXA_API_KEY")
    elif provider == "custom":
        return _raw("CUSTOM_PROVIDER_API_KEY") or _raw("CUSTOM_API_KEY")
    return None


@dataclass
class PathSettings:
    """Resolved, on-disk locations for all persisted framework state.

    Every path is derived from a single master root (``LOGICORE_STORAGE_ROOT``,
    default ``~/.logicore``) unless an explicit per-directory override env var
    is supplied. This is the canonical source the rest of the framework uses
    instead of building ``cwd/.logicore/...`` paths itself.
    """

    storage_root: Path
    memory_dir: Path
    tasks_dir: Path
    sessions_dir: Path
    plans_dir: Path
    snapshots_dir: Path
    assets_dir: Path
    lancedb_path: Path
    database_url: str
    db_password: str
    snapshot_enabled: bool
    media_root: str
    media_s3_endpoint: Optional[str]
    media_s3_access_key: Optional[str]
    media_s3_secret_key: Optional[str]
    media_s3_region: Optional[str]


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
    HOST: str = field(default_factory=lambda: _get_env("HOST", "127.0.0.1", "server", "host"))
    PORT: int = field(default_factory=lambda: _get_int("PORT", 8000, "server", "port"))
    FRONTEND_PORT: int = field(default_factory=lambda: _get_int("FRONTEND_PORT", 3000, "server", "frontend_port"))
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        """Allowed CORS origins."""
        origins = _get_env("CORS_ORIGINS")
        if origins:
            return [o.strip() for o in origins.split(",")]

        return [
            f"http://localhost:{self.PORT}",
            f"http://localhost:{self.FRONTEND_PORT}",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:3000",
        ]
    
    # ==========================================================================
    # EMBEDDING
    # ==========================================================================
    EMBEDDING_PROVIDER: str = field(default_factory=lambda: _get_env("EMBEDDING_PROVIDER", "ollama", "embedding", "provider"))
    """Embedding provider: 'ollama' or 'huggingface'"""
    
    EMBEDDING_MODEL: str = field(default_factory=lambda: _get_env("EMBEDDING_MODEL", "qwen3-embedding:0.6b", "embedding", "model"))
    """Embedding model name"""
    
    @property
    def OLLAMA_URL(self) -> str:
        """Ollama API URL (auto-detects Kubernetes)."""
        return _get_env("OLLAMA_URL", "http://localhost:11434", "embedding", "ollama_url")
    
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
    # RUNTIME (Agentic Loop Configuration)
    # ==========================================================================
    # Turn Management
    RUNTIME_MAX_TURNS: int = field(default_factory=lambda: _get_int("RUNTIME_MAX_TURNS", 60, "runtime", "max_turns"))
    """Maximum turns per session (budget)"""
    
    RUNTIME_WARN_AT_TURNS: int = field(default_factory=lambda: _get_int("RUNTIME_WARN_AT_TURNS", 50, "runtime", "warn_at_turns"))
    """Warn when remaining turns drops to this threshold"""
    
    RUNTIME_DEFAULT_TIMEOUT_MS: int = field(default_factory=lambda: _get_int("RUNTIME_DEFAULT_TIMEOUT_MS", 30000, "runtime", "default_timeout_ms"))
    """Default turn timeout in milliseconds"""
    
    # Loop Detection
    LOOP_DETECTION_ENABLED: bool = field(default_factory=lambda: _get_bool("LOOP_DETECTION_ENABLED", True, "runtime.loop_detection", "enabled"))
    """Enable multi-layer loop detection"""
    
    LOOP_TOOL_THRESHOLD: int = field(default_factory=lambda: _get_int("LOOP_TOOL_THRESHOLD", 5, "runtime.loop_detection", "tool_threshold"))
    """Consecutive identical tool calls before flagging"""
    
    LOOP_CONTENT_THRESHOLD: int = field(default_factory=lambda: _get_int("LOOP_CONTENT_THRESHOLD", 10, "runtime.loop_detection", "content_threshold"))
    """Repeated content chunks before flagging"""
    
    LOOP_LLM_FALLBACK: bool = field(default_factory=lambda: _get_bool("LOOP_LLM_FALLBACK", True, "runtime.loop_detection", "llm_fallback"))
    """Use LLM-based semantic loop detection as fallback"""
    
    # Context Management
    CONTEXT_MAX_TOKENS: int = field(default_factory=lambda: _get_int("CONTEXT_MAX_TOKENS", 128000, "runtime.context", "max_tokens"))
    """Maximum context window tokens"""
    
    CONTEXT_COMPRESS_THRESHOLD: float = field(default_factory=lambda: float(_get_env("CONTEXT_COMPRESS_THRESHOLD", "0.85", "runtime.context", "compress_threshold")))
    """Trigger compression when usage exceeds this ratio (0.0-1.0)"""
    
    CONTEXT_MAX_HISTORY_MESSAGES: int = field(default_factory=lambda: _get_int("CONTEXT_MAX_HISTORY_MESSAGES", 100, "runtime.context", "max_history_messages"))
    """Maximum messages in history before forced truncation"""
    
    CONTEXT_TOOL_OUTPUT_MASK_THRESHOLD: int = field(default_factory=lambda: _get_int("CONTEXT_TOOL_OUTPUT_MASK_THRESHOLD", 30000, "runtime.context", "tool_output_mask_threshold"))
    """Mask tool outputs when total tokens exceed this"""
    
    # Tool Execution
    TOOL_EXECUTION_TIMEOUT: int = field(default_factory=lambda: _get_int("TOOL_EXECUTION_TIMEOUT", 60, "runtime.tool", "execution_timeout"))
    """Tool execution timeout in seconds"""
    
    TOOL_ENABLE_DEDUPLICATION: bool = field(default_factory=lambda: _get_bool("TOOL_ENABLE_DEDUPLICATION", True, "runtime.tool", "enable_deduplication"))
    """Enable tool call deduplication via content hash"""
    
    TOOL_DEDUP_RESULT_CACHE_MAX: int = field(default_factory=lambda: _get_int("TOOL_DEDUP_RESULT_CACHE_MAX", 500, "runtime.tool.dedup", "result_cache_max"))
    """Max entries in persistent result cache (Layer 1)"""
    
    TOOL_DEDUP_RESULT_CACHE_TTL: int = field(default_factory=lambda: _get_int("TOOL_DEDUP_RESULT_CACHE_TTL", 600, "runtime.tool.dedup", "result_cache_ttl"))
    """TTL for persistent result cache entries in seconds (Layer 1)"""
    
    TOOL_DEDUP_FILE_CACHE_MAX: int = field(default_factory=lambda: _get_int("TOOL_DEDUP_FILE_CACHE_MAX", 100, "runtime.tool.dedup", "file_cache_max"))
    """Max entries in file state cache (Layer 3)"""
    
    TOOL_DEDUP_FILE_CACHE_MAX_BYTES: int = field(default_factory=lambda: _get_int("TOOL_DEDUP_FILE_CACHE_MAX_BYTES", 26214400, "runtime.tool.dedup", "file_cache_max_bytes"))
    """Max total bytes for file state cache (Layer 3, default 25MB)"""
    
    TOOL_DEFAULT_COOLDOWN: int = field(default_factory=lambda: _get_int("TOOL_DEFAULT_COOLDOWN", 60, "runtime.tool", "default_cooldown"))
    """Default cooldown duration in seconds after loop detection"""
    
    # Retry
    RETRY_MAX_ATTEMPTS: int = field(default_factory=lambda: _get_int("RETRY_MAX_ATTEMPTS", 3, "runtime.retry", "max_attempts"))
    """Maximum retry attempts for transient failures"""
    
    RETRY_BASE_DELAY_MS: int = field(default_factory=lambda: _get_int("RETRY_BASE_DELAY_MS", 500, "runtime.retry", "base_delay_ms"))
    """Base delay between retries in milliseconds"""
    
    RETRY_EXPONENTIAL_BACKOFF: bool = field(default_factory=lambda: _get_bool("RETRY_EXPONENTIAL_BACKOFF", True, "runtime.retry", "exponential_backoff"))
    """Use exponential backoff for retries"""
    
    # Telemetry
    TELEMETRY_ENABLED: bool = field(default_factory=lambda: _get_bool("TELEMETRY_ENABLED", True, "runtime.telemetry", "enabled"))
    """Enable telemetry collection"""
    
    TELEMETRY_LOG_PROMPTS: bool = field(default_factory=lambda: _get_bool("TELEMETRY_LOG_PROMPTS", False, "runtime.telemetry", "log_prompts"))
    """Include prompts in telemetry logs (privacy sensitive)"""
    
    # Prompt Caching
    PROMPT_CACHE_ENABLED: bool = field(default_factory=lambda: _get_bool("PROMPT_CACHE_ENABLED", True, "runtime.prompt_cache", "enabled"))
    """Enable prompt caching for reduced latency and cost"""
    
    PROMPT_CACHE_TTL_SECONDS: int = field(default_factory=lambda: _get_int("PROMPT_CACHE_TTL_SECONDS", 300, "runtime.prompt_cache", "ttl_seconds"))
    """TTL for prompt cache entries in seconds (default 5 minutes)"""
    
    PROMPT_CACHE_MAX_ENTRIES: int = field(default_factory=lambda: _get_int("PROMPT_CACHE_MAX_ENTRIES", 100, "runtime.prompt_cache", "max_entries"))
    """Maximum number of prompt cache entries"""
    
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

    # ==========================================================================
    # STORAGE (3-tier session persistence)
    # ==========================================================================

    @property
    def paths(self) -> "PathSettings":
        """Canonical, resolved locations for all persisted framework state.

        Every path is derived from ``LOGICORE_STORAGE_ROOT`` (default
        ``~/.logicore``) with optional per-directory overrides. This is what
        the rest of the framework should use instead of building
        ``cwd/.logicore/...`` paths itself.
        """
        root = storage_root()
        memory = _raw("LOGICORE_MEMORY_DIR")
        tasks = _raw("LOGICORE_TASKS_DIR")
        sessions = _raw("LOGICORE_SESSIONS_DIR")
        plans = _raw("LOGICORE_PLANS_DIR")
        snapshots = _raw("LOGICORE_SNAPSHOTS_DIR")
        assets = _raw("LOGICORE_ASSETS_DIR")
        lancedb = _raw("LOGICORE_LANCEDB_PATH")
        db_url = _raw("LOGICORE_STORAGE_DB_URL") or f"sqlite:///{root / 'database' / 'logicore.db'}"
        return PathSettings(
            storage_root=root,
            memory_dir=_expand(memory) if memory else root / "memory",
            tasks_dir=_expand(tasks) if tasks else root / "tasks",
            sessions_dir=_expand(sessions) if sessions else root / "sessions",
            plans_dir=_expand(plans) if plans else root / "plans",
            snapshots_dir=_expand(snapshots) if snapshots else root / "snapshots",
            assets_dir=_expand(assets) if assets else root / "assets",
            lancedb_path=_expand(lancedb) if lancedb else root / "lancedb_data",
            database_url=db_url,
            db_password=_raw("LOGICORE_STORAGE_DB_PASSWORD") or "",
            snapshot_enabled=_get_bool("LOGICORE_STORAGE_SNAPSHOT_ENABLED", True),
            media_root=_raw("LOGICORE_STORAGE_MEDIA_ROOT") or str(root / "assets"),
            media_s3_endpoint=_raw("LOGICORE_STORAGE_S3_ENDPOINT") or None,
            media_s3_access_key=_raw("LOGICORE_STORAGE_S3_ACCESS_KEY") or None,
            media_s3_secret_key=_raw("LOGICORE_STORAGE_S3_SECRET_KEY") or None,
            media_s3_region=_raw("LOGICORE_STORAGE_S3_REGION") or None,
        )

    # --- Backwards-compatible storage accessors (delegating to ``paths``) -----
    @property
    def STORAGE_ROOT(self) -> str:
        """Base directory for session persistence (~/.logicore)."""
        return str(self.paths.storage_root)

    @property
    def STORAGE_DB_URL(self) -> str:
        """Database URL. SQLite if a path, PostgreSQL if postgres://..."""
        return self.paths.database_url

    @property
    def STORAGE_SNAPSHOT_ENABLED(self) -> bool:
        """Enable async snapshot worker (JSON manifests)."""
        return self.paths.snapshot_enabled

    @property
    def STORAGE_MEDIA_ROOT(self) -> str:
        """Binary media root. Local path or s3://bucket/prefix."""
        return self.paths.media_root

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
            "EMBEDDING_PROVIDER": self.EMBEDDING_PROVIDER,
            "EMBEDDING_MODEL": self.EMBEDDING_MODEL,
            "STORAGE_ROOT": self.STORAGE_ROOT,
            "STORAGE_DB_URL": self.STORAGE_DB_URL or "(sqlite default)",
            "STORAGE_SNAPSHOT_ENABLED": self.STORAGE_SNAPSHOT_ENABLED,
            "STORAGE_MEDIA_ROOT": self.STORAGE_MEDIA_ROOT or "(local default)",
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

    def create_storage(self):
        """Create and initialize a StorageManager from current settings."""
        from logicore.storage import StorageManager
        from logicore.storage.config import StorageConfig, DatabaseConfig, SnapshotConfig, MediaConfig
        from pathlib import Path

        root = Path(self.STORAGE_ROOT)
        config = StorageConfig(
            database=DatabaseConfig(
                url=self.STORAGE_DB_URL or f"sqlite:///{root / 'database' / 'logicore.db'}",
            ),
            snapshot=SnapshotConfig(
                enabled=self.STORAGE_SNAPSHOT_ENABLED,
                root=str(root / "snapshots"),
            ),
            media=MediaConfig(
                root=self.STORAGE_MEDIA_ROOT or str(root / "assets"),
                endpoint_url=self.paths.media_s3_endpoint,
                aws_access_key_id=self.paths.media_s3_access_key,
                aws_secret_access_key=self.paths.media_s3_secret_key,
                region=self.paths.media_s3_region,
            ),
        )
        manager = StorageManager(config)
        manager.initialize()
        return manager


# Singleton instance
settings = AgentrySettings()

# Backwards compatibility exports
MODE = settings.MODE
DEBUG = settings.DEBUG
OLLAMA_URL = settings.OLLAMA_URL
LANCEDB_PATH = settings.LANCEDB_PATH

# Alias for new code
LogicoreSettings = AgentrySettings
