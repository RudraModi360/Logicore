"""
ContextEngine: Orchestrator for all context management.

Single entry point for the agent's chat loop. Replaces the legacy
ContextMiddleware with a proper multi-stage pipeline.

Pipeline per LLM call:
  Stage 0: Token estimation and budget check
  Stage 1: Tool output masking (fast, no LLM)
  Stage 2: Compression via LLM summarization
  Stage 3: Emergency truncation (last resort)

Also provides:
  - prepare_messages() — main entry point called before each LLM call
  - distill_tool_result() — per-tool-call truncation
  - assemble_prompt() — system prompt construction
  - inject_hint() / remove_hint() — message injection utilities
  - Prompt caching for reduced latency and cost
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.context.manager import ContextWindowManager, ContextManagementResult
from logicore.context_engine.token_estimator import TokenEstimator
from logicore.context_engine.prompt_assembler import PromptAssembler
from logicore.context_engine.message_pipeline import MessagePipeline
from logicore.context_engine.tool_output_distiller import ToolOutputDistiller
from logicore.caching import PromptCacheManager, get_prompt_cache_manager


@dataclass
class EngineResult:
    """
    Result of a context engine operation.

    Wraps ContextManagementResult with additional metadata
    useful for telemetry and debugging.
    """
    original_tokens: int = 0
    final_tokens: int = 0
    tokens_saved: int = 0
    masked: bool = False
    compressed: bool = False
    truncated: bool = False
    timestamp: datetime = field(default_factory=datetime.now)

    # Sub-results from the pipeline
    _raw: Optional[ContextManagementResult] = None

    @property
    def any_action_taken(self) -> bool:
        return self.masked or self.compressed or self.truncated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "tokens_saved": self.tokens_saved,
            "masked": self.masked,
            "compressed": self.compressed,
            "truncated": self.truncated,
            "timestamp": self.timestamp.isoformat(),
        }


class ContextEngine:
    """
    Unified context management for the agent.

    Usage:
        engine = ContextEngine(config, llm_provider, model_name="gpt-4o")

        # Before each LLM call:
        result, messages = await engine.prepare_messages(messages, session_id)

        # When processing tool results:
        content = engine.distill_tool_result("read_file", result, reused=False)

        # When building system prompt:
        prompt = engine.assemble_prompt(base, tools, skills)
    """

    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
        debug: bool = False,
        telemetry_tracker: Optional[Any] = None,
    ):
        self.config = config
        self.debug = debug
        self.telemetry_tracker = telemetry_tracker

        # Sub-components
        self.token_estimator = TokenEstimator(token_counter)
        self.prompt_assembler = PromptAssembler(
            max_chars=config.context.system_prompt_max_chars,
            debug=debug,
        )
        self.message_pipeline = MessagePipeline()
        self.tool_output_distiller = ToolOutputDistiller(
            max_chars=config.tool.max_output_chars,
        )

        # The heavy lifter: ContextWindowManager from runtime/context
        # This handles masking → compression → truncation pipeline
        self._window_manager = ContextWindowManager(
            config=config,
            llm_provider=llm_provider,
            model_name=model_name,
            token_counter=token_counter,
        )

        # Prompt caching for reduced latency and cost
        prompt_cache_config = getattr(config, 'prompt_cache', None)
        self._prompt_cache = get_prompt_cache_manager(
            enabled=getattr(prompt_cache_config, 'enabled', True) if prompt_cache_config else True,
            ttl_seconds=getattr(prompt_cache_config, 'ttl_seconds', 300) if prompt_cache_config else 300,
            max_entries=getattr(prompt_cache_config, 'max_entries', 100) if prompt_cache_config else 100,
            debug=debug,
        )

        # Per-session results history
        self._results: Dict[str, List[EngineResult]] = {}

    async def prepare_messages(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[EngineResult, List[Dict[str, Any]]]:
        """
        Prepare messages for an LLM call.

        Runs the full context management pipeline:
        1. Estimate current token usage
        2. If over budget, run masking → compression → truncation
        3. Add cache annotations for prompt caching
        4. Return managed messages ready for the API

        This is the MAIN ENTRY POINT called before each LLM call.
        """
        result = EngineResult()

        # Stage 0: Estimate tokens
        result.original_tokens = self.token_estimator.count_messages_tokens(messages)

        # Delegate to ContextWindowManager for the actual pipeline
        try:
            raw_result, managed_messages = await self._window_manager.manage(
                messages, session_id
            )
        except Exception as e:
            # If context management fails (e.g., compression timeout), return original messages
            if self.debug:
                print(f"[ContextEngine] ⚠️ Context management failed: {e}. Using original messages.")
            result.final_tokens = result.original_tokens
            return result, messages

        # Map raw result to our EngineResult
        result._raw = raw_result
        result.final_tokens = self.token_estimator.count_messages_tokens(managed_messages)
        result.tokens_saved = result.original_tokens - result.final_tokens
        result.masked = raw_result.masked
        result.compressed = raw_result.compressed
        result.truncated = raw_result.truncated

        if result.any_action_taken and self.debug:
            print(
                f"[ContextEngine] Pipeline: {result.original_tokens} → {result.final_tokens} tokens "
                f"(saved {result.tokens_saved}). "
                f"masked={result.masked} compressed={result.compressed} truncated={result.truncated}"
            )

        # Stage 4: Add prompt cache annotations
        # Update cache state with current system messages and tools
        system_messages = [m for m in managed_messages if m.get("role") == "system"]
        self._prompt_cache.update_prefix_state(system_messages, tools)
        
        # Annotate messages with cache control metadata
        managed_messages = self._prompt_cache.annotate_messages(managed_messages)

        # Store result for telemetry
        self._store_result(session_id, result)

        return result, managed_messages

    def distill_tool_result(
        self,
        tool_name: str,
        result: Dict[str, Any],
        reused: bool = False,
    ) -> str:
        """
        Distill a tool result for the model context.

        Called per-tool-call to enforce per-result size limits.
        """
        return self.tool_output_distiller.distill(tool_name, result, reused)

    def assemble_prompt(
        self,
        base_prompt: str,
        tools_section: str,
        skills_section: str = "",
        hidden_tool_count: int = 0,
    ) -> str:
        """Assemble the system prompt with truncation."""
        return self.prompt_assembler.assemble(
            base_prompt, tools_section, skills_section, hidden_tool_count
        )

    def patch_custom_prompt(
        self,
        custom_prompt: str,
        tools_section: str,
        skills_section: str = "",
    ) -> str:
        """Patch a custom system prompt by replacing tool sections."""
        return self.prompt_assembler.patch_custom_prompt(
            custom_prompt, tools_section, skills_section
        )

    def inject_hint(
        self,
        messages: List[Dict[str, Any]],
        content: str,
    ) -> int:
        """Inject a system hint into message history."""
        return self.message_pipeline.inject_system_message(messages, content)

    def remove_hint(
        self,
        messages: List[Dict[str, Any]],
        content: str,
    ) -> bool:
        """Remove a system hint from message history."""
        return self.message_pipeline.remove_system_message(messages, content)

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self.token_estimator.count_tokens(text)

    def get_budget_status(self) -> Dict[str, Any]:
        """Get current budget status from the window manager."""
        return self._window_manager.get_budget_status()

    def get_results_history(self, session_id: str) -> List[EngineResult]:
        """Get context management history for a session."""
        return self._results.get(session_id, [])

    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        self._results.pop(session_id, None)
        self._window_manager.clear_session(session_id)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get prompt cache statistics."""
        return self._prompt_cache.get_stats()

    def record_cache_hit(self, session_id: str, tokens_saved: int = 0, cost_saved: float = 0.0) -> None:
        """Record a cache hit for telemetry."""
        self._prompt_cache.record_request(
            tokens_saved=tokens_saved,
            cost_saved=cost_saved,
            cache_hit=True,
        )
        
        # Also record in telemetry tracker if available
        if self.telemetry_tracker:
            self.telemetry_tracker.record_cache_hit(
                session_id=session_id,
                tokens_saved=tokens_saved,
                cost_saved=cost_saved,
            )

    def record_cache_miss(self, session_id: str) -> None:
        """Record a cache miss for telemetry."""
        self._prompt_cache.record_request(cache_hit=False)
        
        # Also record in telemetry tracker if available
        if self.telemetry_tracker:
            self.telemetry_tracker.record_cache_miss(session_id=session_id)

    def clear_cache(self) -> None:
        """Clear prompt cache."""
        self._prompt_cache.clear_cache()

    def _store_result(self, session_id: str, result: EngineResult) -> None:
        """Store result in history."""
        if session_id not in self._results:
            self._results[session_id] = []
        self._results[session_id].append(result)
        # Keep last 100 results
        if len(self._results[session_id]) > 100:
            self._results[session_id] = self._results[session_id][-100:]
