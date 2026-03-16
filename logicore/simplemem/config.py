"""
SimpleMem configuration for logicore.
Adapts to LOCAL or CLOUD mode automatically.
"""
import os
from pathlib import Path


def get_mode() -> str:
    """Get deployment mode."""
    return os.getenv("AGENTRY_MODE", "local").lower()


def get_lancedb_path() -> str:
    """
    Get LanceDB storage path based on mode.
    
    LOCAL: ./logicore/user_data/lancedb_data/
    CLOUD: Uses LANCEDB_PATH env var (can be S3, Azure Blob, etc.)
    """
    mode = get_mode()
    
    if mode == "cloud":
        # Cloud storage path from environment
        cloud_path = os.getenv("LANCEDB_PATH")
        if cloud_path:
            return cloud_path
    
    # Default: Local path
    base_dir = Path(__file__).parent.parent
    local_path = base_dir / "user_data" / "lancedb_data"
    local_path.mkdir(parents=True, exist_ok=True)
    return str(local_path)


def get_ollama_url() -> str:
    """
    Get Ollama service URL.
    
    LOCAL: http://localhost:11434
    CLOUD: http://ollama-service:11434 (Kubernetes internal service)
    """
    return os.getenv("OLLAMA_URL", "http://localhost:11434")


def get_embedding_config() -> dict:
    """Get embedding model configuration."""
    return {
        "provider": os.getenv("EMBEDDING_PROVIDER", "ollama"),
        "model": os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        "ollama_url": get_ollama_url(),
    }


# Memory building configuration
WINDOW_SIZE = int(os.getenv("SIMPLEMEM_WINDOW_SIZE", "6"))
ENABLE_PARALLEL_PROCESSING = os.getenv("SIMPLEMEM_PARALLEL", "true").lower() == "true"
MAX_PARALLEL_WORKERS = int(os.getenv("SIMPLEMEM_MAX_WORKERS", "4"))

# Retrieval configuration
SEMANTIC_TOP_K = int(os.getenv("SIMPLEMEM_TOP_K", "5"))
ENABLE_PLANNING = False  # Disabled for fast retrieval
ENABLE_REFLECTION = False  # Disabled for fast retrieval


def get_min_store_score() -> int:
    """Minimum signal score required to persist a memory item."""
    return int(os.getenv("SIMPLEMEM_MIN_STORE_SCORE", "3"))


def get_min_retrieve_score() -> int:
    """Minimum score required to inject a memory item into context."""
    return int(os.getenv("SIMPLEMEM_MIN_RETRIEVE_SCORE", "2"))


def get_max_facts_per_turn() -> int:
    """Maximum number of atomic facts extracted from one dialogue turn."""
    return int(os.getenv("SIMPLEMEM_MAX_FACTS_PER_TURN", "3"))


def get_max_memory_chars() -> int:
    """Maximum character length for one stored memory fact."""
    return int(os.getenv("SIMPLEMEM_MAX_MEMORY_CHARS", "220"))

# Table naming
def get_memory_table_name(user_id: str, session_id: str = None, isolate_by_session: bool = True) -> str:
    """Get memory table name with optional per-session isolation."""
    safe_user = "".join(c if c.isalnum() else "_" for c in str(user_id))

    if isolate_by_session:
        safe_session = "".join(c if c.isalnum() else "_" for c in str(session_id or "default"))
        return f"memories_{safe_user}_{safe_session}"

    return f"memories_{safe_user}"
