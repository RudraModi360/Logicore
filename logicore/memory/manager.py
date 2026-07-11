"""
Memory context injection integration.

Integrates memory retrieval and extraction into the agent's context engine,
providing automatic memory surfacing and extraction.
"""

import os
import asyncio
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import logging

from logicore.config import settings
from logicore.config.env import _raw
from logicore.memory.types import MemoryMetadata
from logicore.memory.storage import MemoryStore
from logicore.memory.retrieval.retriever import MemoryRetriever
from logicore.memory.extraction.worker import ExtractionWorker

logger = logging.getLogger(__name__)

# Default global memory path (resolved through logicore.config).
DEFAULT_MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".logicore", "memory")

# Environment variable for custom memory path
MEMORY_DIR_ENV_VAR = "LOGICORE_MEMORY_DIR"


def resolve_memory_dir(custom_path: Optional[str] = None) -> str:
    """
    Resolve the memory directory path.

    Priority:
    1. Custom path (explicit parameter)
    2. Environment variable (LOGICORE_MEMORY_DIR)
    3. Centralized config default (settings.paths.memory_dir → ~/.logicore/memory)

    Args:
        custom_path: Optional custom path to memory directory

    Returns:
        Resolved memory directory path
    """
    # 1. Explicit custom path
    if custom_path:
        return custom_path

    # 2. Environment variable (explicit override over the config default)
    env_path = _raw(MEMORY_DIR_ENV_VAR)
    if env_path:
        return env_path

    # 3. Centralized config default
    return str(settings.paths.memory_dir)


class MemoryManager:
    """
    Central manager for memory subsystem.
    
    Coordinates storage, retrieval, extraction, and context injection.
    Memory is stored globally at ~/.logicore/memory/ by default,
    making it available across all sessions.
    """
    
    def __init__(
        self,
        memory_dir: Optional[str] = None,
        llm_provider: Any = None,
        llm_model: Optional[str] = None,
        debug: bool = False,
        enabled: bool = True,
        throttle_interval: float = 1.0,
        transcript_window: int = 20,
        persistence_path: Optional[str] = None,
    ):
        """
        Initialize the memory manager.

        Args:
            memory_dir: Path to memory directory. If None, uses global default.
                        Can also be set via LOGICORE_MEMORY_DIR env var.
            llm_provider: LLM provider for extraction. May be a provider
                *instance*, or a provider *name* string (e.g. "ollama").
                When a name is given, `llm_model` selects the model to use
                for memory tasks (independent of the main chat model).
            llm_model: Optional model name for the memory LLM. Allows using a
                different/cheaper model for extraction & retrieval than the
                main agent.
            debug: Enable debug logging
            enabled: Whether memory is enabled
            throttle_interval: Seconds between background extractions
            transcript_window: Max recent messages sent to the extraction LLM
            persistence_path: Where to persist the pending extraction queue
        """
        self.enabled = enabled
        self.debug = debug

        # Resolve memory directory
        self.memory_dir = resolve_memory_dir(memory_dir)

        if self.debug:
            logger.debug(f"[MemoryManager] Memory directory: {self.memory_dir}")

        if not enabled:
            self.store = None
            self.retriever = None
            self.worker = None
            self._llm_provider = None
            return

        self.store = MemoryStore(self.memory_dir, debug=debug)

        # Resolve the provider (instance or name + model)
        self._llm_provider = self._resolve_provider(llm_provider, llm_model)

        self.retriever = MemoryRetriever(
            self.store, debug=debug, llm_provider=self._llm_provider
        )

        # Initialize extraction worker if LLM provider is available
        self.worker = None
        if self._llm_provider:
            self.worker = ExtractionWorker(
                memory_store=self.store,
                llm_provider=self._llm_provider,
                debug=debug,
                on_extract=self._on_extraction_complete,
                throttle_interval=throttle_interval,
                transcript_window=transcript_window,
                persistence_path=persistence_path,
            )

    @staticmethod
    def _resolve_provider(llm_provider: Any, llm_model: Optional[str]) -> Any:
        """
        Resolve the LLM provider for memory tasks.

        Accepts either an already-constructed provider instance, or a
        provider name string. In the latter case a provider is created via
        the factory, optionally with a dedicated model name.
        """
        if llm_provider is None:
            return None
        # Already an instance
        if not isinstance(llm_provider, str):
            return llm_provider
        # Provider name -> create instance (with optional dedicated model)
        try:
            from logicore.providers.factory import create_provider
            return create_provider(llm_provider, model=llm_model)
        except Exception as e:
            logger.warning(
                f"[MemoryManager] Failed to create provider '{llm_provider}': {e}"
            )
            return None
    
    async def start(self) -> None:
        """Start the memory manager (extraction worker)."""
        if not self.enabled or not self.worker:
            return
        
        await self.worker.start()
    
    async def stop(self) -> None:
        """Stop the memory manager."""
        if not self.enabled or not self.worker:
            return
        
        await self.worker.stop()
    
    def get_memory_prompt_section(self) -> str:
        """
        Get memory instructions for system prompt.
        
        Returns:
            Memory section for system prompt
        """
        if not self.enabled or not self.store:
            return ""
        
        index_content = self.store.read_index()
        
        return f"""
## Persistent Memory

You have access to persistent memory that persists across sessions.
Memory is stored at: {self.memory_dir}

MEMORY TYPES:
- user: User's role, goals, responsibilities (identity)
- feedback: Corrections and confirmations (preferences)
- project: Ongoing work not derivable from code (knowledge)
- reference: Pointers to external systems (knowledge)
- domain: Domain-specific knowledge
- additional_learning: Insights, patterns discovered

REMEMBER:
- Only save genuinely useful information
- Do not duplicate existing memories
- Use appropriate importance and stability levels
- Include relevant tags

ACCESS MEMORY:
- Memories are automatically surfaced based on relevance
- Use the memory tools to explicitly save important information

EXISTING MEMORY INDEX:
{index_content if index_content else "No memories yet."}
"""
    
    async def inject_context(
        self,
        messages: List[Dict[str, Any]],
        user_input: str,
        use_llm_selection: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Inject relevant memories into message context.

        Args:
            messages: Current message list
            user_input: Current user input
            use_llm_selection: Use LLM-based final selection for high-stakes
                queries (recall/question intent)

        Returns:
            Modified messages with memory context injected
        """
        if not self.enabled or not self.retriever:
            return messages

        # Retrieve relevant memories (LLM-aware async path)
        memories = await self.retriever.aretrieve(
            user_input, use_llm_selection=use_llm_selection
        )

        if not memories:
            return messages
        
        # Format for injection
        injection = self.retriever.format_for_injection(memories)
        
        if not injection:
            return messages
        
        # Inject as system reminder before user message
        injection_msg = {
            "role": "user",
            "content": f"<system-reminder>\n{injection}\n</system-reminder>",
        }
        
        # Find the last user message and inject before it
        insert_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                insert_idx = i
                break
        
        messages.insert(insert_idx, injection_msg)
        
        return messages
    
    async def submit_for_extraction(
        self,
        conversation: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> None:
        """
        Submit conversation for background extraction.
        
        Args:
            conversation: Conversation messages
            session_id: Session identifier
        """
        if not self.enabled or not self.worker:
            return
        
        await self.worker.submit_conversation(conversation, session_id)
    
    def _on_extraction_complete(self, memories: List[MemoryMetadata]) -> None:
        """Callback after successful extraction."""
        if self.debug:
            logger.debug(
                f"[MemoryManager] Extraction complete: {len(memories)} memories"
            )
    
    def reset_session(self) -> None:
        """Reset session state (e.g., after compact)."""
        if self.retriever:
            self.retriever.reset_session()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory manager statistics."""
        stats = {
            "enabled": self.enabled,
            "memory_dir": self.memory_dir,
        }
        
        if self.enabled:
            if self.store:
                headers = self.store.scan_memory_files()
                stats["memory_count"] = len(headers)
            
            if self.retriever:
                stats["retrieval"] = self.retriever.get_stats()
            
            if self.worker:
                stats["extraction"] = self.worker.get_stats()
        
        return stats


# Global memory manager instance
_global_manager: Optional[MemoryManager] = None


def get_memory_manager(
    memory_dir: Optional[str] = None,
    llm_provider: Any = None,
    llm_model: Optional[str] = None,
    debug: bool = False,
    enabled: bool = True,
    throttle_interval: float = 1.0,
    transcript_window: int = 20,
    persistence_path: Optional[str] = None,
) -> MemoryManager:
    """
    Get or create the global memory manager.

    Memory is stored globally at ~/.logicore/memory/ by default,
    making it available across all sessions.

    Can be customized via:
    - memory_dir parameter
    - LOGICORE_MEMORY_DIR environment variable

    Args:
        memory_dir: Optional custom path to memory directory
        llm_provider: LLM provider for extraction (instance or name)
        llm_model: Optional dedicated model for memory tasks
        debug: Enable debug logging
        enabled: Whether memory is enabled
        throttle_interval: Seconds between background extractions
        transcript_window: Max recent messages sent to extraction LLM
        persistence_path: Where to persist the pending extraction queue

    Returns:
        MemoryManager instance
    """
    global _global_manager

    if _global_manager is None:
        _global_manager = MemoryManager(
            memory_dir=memory_dir,
            llm_provider=llm_provider,
            llm_model=llm_model,
            debug=debug,
            enabled=enabled,
            throttle_interval=throttle_interval,
            transcript_window=transcript_window,
            persistence_path=persistence_path,
        )

    return _global_manager


def reset_memory_manager() -> None:
    """Reset the global memory manager."""
    global _global_manager
    _global_manager = None
