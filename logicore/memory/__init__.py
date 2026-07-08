"""
Memory subsystem for persistent memory across sessions.

Provides:
- Memory storage with YAML frontmatter
- LLM-based extraction worker
- Memory retrieval with decay scoring
- Context injection integration
- Consolidation and forgetting mechanisms
"""

# Lazy imports to avoid circular dependencies
__all__ = [
    # Types
    "MemoryDomain",
    "MemoryKind",
    "MemoryStability",
    "MemoryType",
    "MemoryMetadata",
    "MemoryHeader",
    "TopicDetection",
    "MemoryScore",
    
    # Components
    "MemoryStore",
    "ExtractionWorker",
    "MemoryRetriever",
    "ConsolidationWorker",
    "MemoryManager",
    
    # Manager functions
    "get_memory_manager",
    "reset_memory_manager",
]


def __getattr__(name):
    """Lazy import to avoid circular dependencies."""
    if name in ("MemoryDomain", "MemoryKind", "MemoryStability", "MemoryType",
                "MemoryMetadata", "MemoryHeader", "TopicDetection", "MemoryScore"):
        from logicore.memory.types import (
            MemoryDomain, MemoryKind, MemoryStability, MemoryType,
            MemoryMetadata, MemoryHeader, TopicDetection, MemoryScore,
        )
        return locals()[name]
    
    if name == "MemoryStore":
        from logicore.memory.storage import MemoryStore
        return MemoryStore
    
    if name == "ExtractionWorker":
        from logicore.memory.extraction.worker import ExtractionWorker
        return ExtractionWorker
    
    if name == "MemoryRetriever":
        from logicore.memory.retrieval.retriever import MemoryRetriever
        return MemoryRetriever
    
    if name == "ConsolidationWorker":
        from logicore.memory.consolidation.worker import ConsolidationWorker
        return ConsolidationWorker
    
    if name in ("MemoryManager", "get_memory_manager", "reset_memory_manager"):
        from logicore.memory.manager import MemoryManager, get_memory_manager, reset_memory_manager
        if name == "MemoryManager":
            return MemoryManager
        elif name == "get_memory_manager":
            return get_memory_manager
        else:
            return reset_memory_manager
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
