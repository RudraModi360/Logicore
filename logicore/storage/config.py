"""
StorageConfig: Configuration for the 3-tier storage system.

Provides configuration for:
- SQL database (canonical source of truth)
- Snapshot synchronization (async, stateless)
- Binary media storage (attachments, files)

Default directory: ~/.logicore/

Usage:
    from logicore.storage.config import StorageConfig

    # Default local config
    config = StorageConfig()

    # Custom paths
    config = StorageConfig(
        db_url="~/.logicore/database/logicore.db",
        snapshot_root="~/.logicore/snapshots",
        assets_root="~/.logicore/assets",
    )

    # Cloud database
    config = StorageConfig(
        db_url="postgresql://user:pass@host/logicore",
        db_password="secret",
    )
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# All storage paths are owned by logicore.config (single .env gateway).
from logicore.config import settings


def _expand_home(path: str) -> Path:
    """Expand ~ to user home directory."""
    return Path(os.path.expanduser(path))


def _default_base_dir() -> Path:
    """Default base directory for all storage tiers.

    Resolved through ``logicore.config`` (``LOGICORE_STORAGE_ROOT``, default
    ``~/.logicore``) so a single global parameter controls where state lives.
    """
    return settings.paths.storage_root


@dataclass
class DatabaseConfig:
    """Configuration for SQL database backend."""

    # Database URL. Supports:
    #   sqlite:///path/to/db.sqlite  (local)
    #   postgresql://user:pass@host/db  (cloud)
    url: str = ""

    # Password for database connection (optional, for cloud DBs)
    password: str = ""

    # Connection pool size (for cloud databases)
    pool_size: int = 5

    def __post_init__(self):
        if not self.url:
            base = _default_base_dir()
            db_path = base / "database" / "logicore.db"
            self.url = f"sqlite:///{db_path}"
        elif not self.url.startswith(("sqlite:///", "postgresql://", "postgres://")):
            # Plain path like "/tmp/test.db" or "C:\...\test.db" → treat as SQLite
            self.url = f"sqlite:///{self.url}"

    @property
    def is_sqlite(self) -> bool:
        """Check if this is a SQLite database."""
        return self.url.startswith("sqlite:///")

    @property
    def is_postgresql(self) -> bool:
        """Check if this is a PostgreSQL database."""
        return self.url.startswith(("postgresql://", "postgres://"))

    @property
    def sqlite_path(self) -> Optional[Path]:
        """Get the SQLite file path (only for SQLite databases)."""
        if not self.is_sqlite:
            return None
        path_str = self.url.replace("sqlite:///", "")
        return _expand_home(path_str)


@dataclass
class SnapshotConfig:
    """Configuration for snapshot synchronization."""

    # Enable/disable snapshot sync
    enabled: bool = True

    # Root directory for snapshot files
    root: str = ""

    # Enable local filesystem snapshots
    local_snapshot: bool = True

    def __post_init__(self):
        if not self.root:
            self.root = str(_default_base_dir() / "snapshots")

    @property
    def root_path(self) -> Path:
        """Get root as Path object."""
        return _expand_home(self.root)


@dataclass
class MediaConfig:
    """Configuration for binary media storage."""

    # Root directory for media files.
    #   Local:  "/abs/path" or "~/.logicore/assets"
    #   S3:     "s3://my-bucket/prefix"
    #   Supabase S3: "s3://my-bucket/prefix" + endpoint/key/secret below
    root: str = ""

    # Enable local filesystem storage
    local_storage: bool = True

    # Max file size in bytes (default 100MB)
    max_file_size: int = 100 * 1024 * 1024

    # S3 / S3-compatible endpoint config (Supabase Storage, MinIO, R2, etc.)
    # For Supabase: endpoint = https://<project-ref>.supabase.co/storage/v1/s3
    endpoint_url: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    region: Optional[str] = None

    def __post_init__(self):
        if not self.root:
            self.root = str(_default_base_dir() / "assets")

    @property
    def is_s3(self) -> bool:
        """True when media root targets an S3/S3-compatible bucket."""
        return self.root.startswith("s3://")

    @property
    def root_path(self) -> Path:
        """Get root as Path object (local only; raises for s3://)."""
        if self.is_s3:
            raise ValueError("Cannot resolve a local path for an s3:// media root")
        return _expand_home(self.root)


@dataclass
class StorageConfig:
    """
    Master configuration for the 3-tier storage system.

    Tiers:
        1. SQL Database — canonical source of truth
        2. Snapshot Sync — async, stateless JSON manifests
        3. Binary Media — file bytes (local or cloud)
    """

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)
    media: MediaConfig = field(default_factory=MediaConfig)

    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist."""
        dirs = []
        if self.database.is_sqlite:
            dirs.append(self.database.sqlite_path.parent)
        dirs.append(self.snapshot.root_path)
        if not self.media.is_s3:
            dirs.append(self.media.root_path)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "StorageConfig":
        """Build configuration from the centralized ``logicore.config`` settings.

        The master ``LOGICORE_STORAGE_ROOT`` sets the base directory for all
        tiers; the more specific ``LOGICORE_STORAGE_*`` vars override
        individual tiers on top of that root. All values come from
        ``settings.paths`` — no direct ``os.environ`` reads here.
        """
        paths = settings.paths
        return cls(
            database=DatabaseConfig(
                url=paths.database_url,
                password=paths.db_password,
            ),
            snapshot=SnapshotConfig(
                enabled=paths.snapshot_enabled,
                root=str(paths.snapshots_dir),
            ),
            media=MediaConfig(
                root=str(paths.assets_dir),
            ),
        )
