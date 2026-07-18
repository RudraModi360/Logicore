"""
Logicore Storage: 3-tier session persistence system.

Tiers:
    1. SQL Database — canonical source of truth
    2. Snapshot Sync — async, stateless JSON manifests
    3. Binary Media — file bytes (local or cloud)

Usage:
    from logicore.storage import StorageManager, StorageConfig

    config = StorageConfig()
    manager = StorageManager(config)
    manager.initialize()

    # Save session
    manager.save_session("s1", messages=[...], provider="openai", model="gpt-4")

    # Load session
    messages = manager.load_session("s1")

    # Attachments
    info = manager.save_attachment("s1", "file1", b"data", "image/png")
"""

from .config import StorageConfig, DatabaseConfig, SnapshotConfig, MediaConfig
from .manager import StorageManager
from .db.base import DatabaseBackend
from .snapshot.base import SnapshotBackend
from .snapshot.manifest import SessionManifest, AttachmentRef
from .snapshot.worker import SnapshotWorker
from .media.base import MediaBackend, MediaInfo


def create_storage(
    root: str = None,
    db: "DatabaseBackend" = None,
    snapshot: "SnapshotBackend" = None,
    media: "MediaBackend" = None,
    enable_snapshot_sync: bool = True,
    **kwargs,
) -> StorageManager:
    """Create and initialize a StorageManager with sane defaults.

    Args:
        root: Base directory (default: ~/.logicore). Sets snapshot and media roots.
        db: Inject a custom DatabaseBackend (e.g. a third-party cloud DB
            connector). When provided, URL-based auto-detection is skipped.
        snapshot: Inject a custom SnapshotBackend.
        media: Inject a custom MediaBackend.
        enable_snapshot_sync: Start the background snapshot worker. Set to
            ``False`` for simple agents that don't need snapshot/memory sync
            — avoids spinning up a background thread that could block exit.
        **kwargs: Override for DatabaseConfig, SnapshotConfig, or MediaConfig fields.
    """
    config = StorageConfig()
    if root:
        config.snapshot.root = str(Path(root) / "snapshots")
        config.media.root = str(Path(root) / "assets")
        db_dir = Path(root) / "database"
        db_dir.mkdir(parents=True, exist_ok=True)
        config.database.url = f"sqlite:///{db_dir / 'logicore.db'}"
    for k, v in kwargs.items():
        if hasattr(config.database, k):
            setattr(config.database, k, v)
        elif hasattr(config.snapshot, k):
            setattr(config.snapshot, k, v)
        elif hasattr(config.media, k):
            setattr(config.media, k, v)
    manager = StorageManager(
        config, db=db, snapshot=snapshot, media=media,
        enable_snapshot_sync=enable_snapshot_sync,
    )
    manager.initialize()
    return manager


__all__ = [
    "StorageConfig",
    "DatabaseConfig",
    "SnapshotConfig",
    "MediaConfig",
    "StorageManager",
    "DatabaseBackend",
    "SnapshotBackend",
    "SessionManifest",
    "AttachmentRef",
    "SnapshotWorker",
    "MediaBackend",
    "MediaInfo",
    "create_storage",
]
