"""
Agentry Memory Module

The old memory system (VFS, SQLite storage, middleware) has been replaced
by SimpleMem integration for better context engineering.

For memory features, use:
- logicore.simplemem.AgentrySimpleMem - Main memory integration
- backend.services.storage - Unified storage interface

Legacy project_memory.py is retained for SmartAgent project mode.
"""

# Keep project_memory for SmartAgent compatibility
from .project_memory import ProjectMemory

__all__ = [
    "ProjectMemory",
]
