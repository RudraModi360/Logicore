"""
SimpleMem integration for logicore.

Provides context engineering capabilities through:
- Semantic Lossless Compression (write-time processing)  
- Fast embedding-based retrieval (read-time)
- LanceDB vector storage

Special thanks to SimpleMem (https://github.com/aiming-lab/SimpleMem)
"""

from .integration import AgentrySimpleMem
from .config import get_lancedb_path, get_embedding_config

__all__ = [
    "AgentrySimpleMem",
    "get_lancedb_path",
    "get_embedding_config",
]
