"""
Tool Deduplication Layer

Three-layer deduplication system to prevent redundant tool executions:
  Layer 1: Static hash-based dedup (bit-by-bit comparison)
  Layer 2: Semantic tool call analysis (same-meaning, different-args)
  Layer 3: File read deduplication (mtime + offset matching)
"""

from .hash_engine import hash_content, hash_tool_call, hash_pair
from .file_state_cache import FileStateCache, file_state_cache
from .result_cache import ResultCache, result_cache
from .semantic_analyzer import SemanticAnalyzer, semantic_analyzer

__all__ = [
    "hash_content",
    "hash_tool_call",
    "hash_pair",
    "FileStateCache",
    "file_state_cache",
    "ResultCache",
    "result_cache",
    "SemanticAnalyzer",
    "semantic_analyzer",
]
