"""
ContextWindowManager: Orchestrates context budget and compression.

Combines:
- TokenBudget: Track usage across categories
- CompressionService: Summarize old messages
- ToolOutputMaskingService: Mask bulky tool outputs

Key improvements over original context_middleware.py:
1. Model-specific context window awareness
2. Multi-stage compression (masking first, then summarization)
3. Async-safe processing
4. Comprehensive telemetry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable
import logging
import re

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.context.token_budget import TokenBudget, TokenCategory
from logicore.runtime.context.compression import CompressionService, CompressionResult, CompressionStatus
from logicore.runtime.context.masking import ToolOutputMaskingService, MaskingResult

from logicore.utils.colors import colored, warning, info, success, error, banner, step

logger = logging.getLogger(__name__)


@dataclass
class ContextManagementResult:
    """Result of context management operation."""
    original_tokens: int = 0
    final_tokens: int = 0
    tokens_saved: int = 0
    
    # Sub-operation results
    masking_result: Optional[MaskingResult] = None
    compression_result: Optional[CompressionResult] = None
    
    # Actions taken
    masked: bool = False
    compressed: bool = False
    truncated: bool = False
    
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def any_action_taken(self) -> bool:
        """Check if any context management action was taken."""
        return self.masked or self.compressed or self.truncated
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "tokens_saved": self.tokens_saved,
            "masked": self.masked,
            "compressed": self.compressed,
            "truncated": self.truncated,
            "masking_result": self.masking_result.to_dict() if self.masking_result else None,
            "compression_result": self.compression_result.to_dict() if self.compression_result else None,
            "timestamp": self.timestamp.isoformat(),
        }


class ContextWindowManager:
    """
    Manages context window budget through compression and masking.
    
    Strategy:
    1. Track token usage via TokenBudget
    2. When threshold approached, first try tool output masking
    3. If still over threshold, compress older messages
    4. As last resort, truncate oldest messages
    
    Usage:
        manager = ContextWindowManager(config, llm_provider, model_name="gpt-4")
        
        # Manage context before LLM call
        result, managed_messages = await manager.manage(messages, session_id)
        
        if result.any_action_taken:
            print(f"Saved {result.tokens_saved} tokens")
        
        # Use managed_messages for LLM call
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
        session_provider: Optional[Callable[[str], Optional[Any]]] = None,
    ):
        """
        Args:
            config: Runtime configuration
            llm_provider: LLM for compression (also used for context_window override)
            model_name: Model name for context window lookup
            token_counter: Optional custom token counter
            session_provider: Optional callable(session_id) -> session with a
                mutable ``messages`` list. When set, compacted context is written
                back to the live session after every managed turn (Bug 3 fix),
                so long-horizon sessions actually shrink across turns.
        """
        self.config = config
        self.model_name = model_name
        self._session_provider = session_provider
        from logicore.runtime.context.token_estimator import TokenEstimator
        provider_name = getattr(llm_provider, "provider_name", "unknown") if llm_provider else "unknown"
        if isinstance(token_counter, TokenEstimator):
            self._estimator = token_counter
        elif token_counter is not None:
            self._estimator = TokenEstimator(token_counter)
        else:
            self._estimator = TokenEstimator(provider=provider_name, model=model_name)
        self._token_counter = self._estimator.count_tokens
        
        context_window = config.get_model_context_window(model_name, provider=llm_provider)
        self.config.context.compression_threshold_tokens = (
            config.get_compression_threshold_for_model(model_name, llm_provider)
        )
        self.config.context.min_prunable_tokens = max(
            500, min(self.config.context.min_prunable_tokens, context_window // 8)
        )

        # Initialize sub-services (pass the provider-aware estimator so all
        # threshold decisions use the same tokenizer).
        self.budget = TokenBudget(config, model_name, self._estimator, provider=llm_provider)
        self.compression_service = CompressionService(
            config,
            llm_provider,
            self._estimator,
            model_name=model_name,
        )
        self.masking_service = ToolOutputMaskingService(config, self._estimator)
        
        # Results history per session
        self._results: Dict[str, List[ContextManagementResult]] = {}
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._token_counter(text)
    
    def _estimate_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens in messages (single source of truth: TokenEstimator).

        Delegates to the framework estimator so tool_calls, role overhead, and
        conversation priming are counted exactly once, consistently with the
        budget and masking. The old local loop half-counted tool_calls and
        skipped role overhead, which made the budget blind to ~48% of the prompt.
        """
        return self._estimator.count_messages_tokens(messages)
    
    def _categorize_tokens(self, messages: List[Dict[str, Any]]) -> Dict[TokenCategory, int]:
        """Categorize tokens by type (single source of truth: TokenEstimator).

        Delegates to the estimator's categorize_tokens (which counts tool_calls
        via count_message_tokens) and maps its string keys to TokenCategory.
        The old local loop dropped tool_calls entirely, so the TOOL_RESULTS /
        MESSAGES split (and thus should_compress / should_mask) was wrong.
        """
        raw = self._estimator.categorize_tokens(messages)
        return {
            TokenCategory.SYSTEM: raw.get("system", 0),
            TokenCategory.TOOLS: raw.get("tools", 0),
            TokenCategory.TOOL_RESULTS: raw.get("tool_results", 0),
            TokenCategory.MESSAGES: raw.get("messages", 0),
        }
    
    async def manage(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> tuple[ContextManagementResult, List[Dict[str, Any]]]:
        """
        Manage context window for messages.
        
        Applies masking and compression as needed to keep within budget.
        
        Args:
            messages: Message history
            session_id: Session identifier
        
        Returns:
            Tuple of (ContextManagementResult, managed messages)
        """
        result = ContextManagementResult()
        current_messages = messages.copy()
        
        # Initial token count
        result.original_tokens = self._estimate_messages_tokens(current_messages)
        
        # Update budget tracking
        categories = self._categorize_tokens(current_messages)
        for category, tokens in categories.items():
            self.budget.set_tokens(category, tokens)
        
        # Check if management needed
        will_mask = self.budget.should_mask_tool_outputs()
        will_compress = self.budget.should_compress()
        if will_mask or will_compress:
            logger.info(
                f"[ContextWindowManager] Context at "
                f"{result.original_tokens}/{self.budget.compression_threshold} tokens - "
                f"starting context management pipeline "
                f"(masking={'on' if will_mask else 'off'}, "
                f"compression={'on' if will_compress else 'off'})..."
            )
            print(
                f"\n{warning('[ContextWindowManager]')} Context "
                f"{result.original_tokens}/{self.budget.compression_threshold} tokens - "
                f"starting context management..."
            )
        else:
            logger.debug(
                "[ContextWindowManager] Context %d/%d tokens - within budget, no management needed",
                result.original_tokens,
                self.budget.compression_threshold,
            )
        
        # Stage 1: Tool output masking (fast, doesn't need LLM)
        if self.budget.should_mask_tool_outputs():
            masking_result, current_messages = self.masking_service.mask(
                current_messages,
                session_id,
            )
            
            if masking_result.masked_count > 0:
                result.masked = True
                result.masking_result = masking_result
                
                # Recount tokens
                new_tokens = self._estimate_messages_tokens(current_messages)
                result.tokens_saved += result.original_tokens - new_tokens
                
                # Update budget
                categories = self._categorize_tokens(current_messages)
                for category, tokens in categories.items():
                    self.budget.set_tokens(category, tokens)
        
        # Stage 2: Compression (needs LLM), only after masking has been applied.
        compress_now = self.budget.should_compress()
        if compress_now:
            print(info("[ContextWindowManager]") + " Running context compression (this may take a moment)...")
            compression_result = await self.compression_service.compress(
                current_messages,
                session_id,
            )
        else:
            compression_result = CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=self._estimate_messages_tokens(current_messages),
                compressed_tokens=self._estimate_messages_tokens(current_messages),
                messages_preserved=len(current_messages),
            )

        if compression_result.status == CompressionStatus.SUCCESS:
            current_messages = self.compression_service.build_compressed_messages(
                current_messages,
                compression_result,
            )
            result.compressed = True
            result.compression_result = compression_result
            result.tokens_saved += compression_result.tokens_saved
            print(
                f"{success('[ContextWindowManager]')} Compression complete: "
                f"{result.original_tokens} → {self._estimate_messages_tokens(current_messages)} tokens"
            )
        elif compress_now:
            print(
                f"{warning('[ContextWindowManager]')} Compression {compression_result.status.value}: "
                f"{self._skip_reason(compression_result, current_messages)}"
            )
        else:
            logger.debug(
                "[ContextWindowManager] Compression not attempted (%d/%d tokens)",
                self._estimate_messages_tokens(current_messages),
                self.budget.compression_threshold,
            )

        # Stage 3: Emergency truncation if still over budget
        current_tokens = self._estimate_messages_tokens(current_messages)
        target_tokens = self.budget.get_compression_target()
        
        if current_tokens > target_tokens * 1.5:
            # Too large even after compression - truncate toward the compression target
            current_messages = self._truncate_messages(
                current_messages,
                target_tokens,
            )
            result.truncated = True
            current_tokens = self._estimate_messages_tokens(current_messages)

        hard_limit = self._hard_limit_tokens()
        if current_tokens > hard_limit:
            current_messages = self._truncate_messages(current_messages, hard_limit)
            result.truncated = True
            logger.warning(
                "[ContextWindowManager] Prompt %d tokens over hard limit %d - "
                "emergency truncation applied, sending truncated context anyway",
                current_tokens,
                hard_limit,
            )

        if result.any_action_taken and self._session_provider is not None:
            self._sync_back_to_session(session_id, current_messages)

        # Final count
        result.final_tokens = self._estimate_messages_tokens(current_messages)
        result.tokens_saved = result.original_tokens - result.final_tokens
        
        # Record snapshot for tracking
        self.budget.record_snapshot()
        self._store_result(session_id, result)
        
        return result, current_messages
    
    def _skip_reason(
        self,
        result: CompressionResult,
        messages: List[Dict[str, Any]],
    ) -> str:
        """Human-readable reason a compression attempt produced no summary."""
        if result.error:
            return result.error
        if result.status != CompressionStatus.SKIPPED_SMALL:
            return result.status.value
        threshold = self.compression_service.compression_threshold_tokens
        tokens = self._estimate_messages_tokens(messages)
        if tokens < threshold:
            return f"below threshold ({tokens}/{threshold} tokens)"
        preserve = self.config.context.preserve_recent_count
        return (
            f"not enough history to split "
            f"({len(messages)} messages, need more than {preserve + 2})"
        )
    
    def _hard_limit_tokens(self) -> int:
        """Enforced prompt cap: (window - reserve) * (1 - margin).

        Kept below the provider window so the model always retains response
        headroom. Margin defaults to 0.05 (env LOGICORE_HARD_LIMIT_MARGIN).
        """
        margin = self.config.context.hard_limit_margin
        return int(
            (self.budget.context_window - self.budget.reserved_for_response)
            * (1.0 - margin)
        )

    def _trim_message_to_fit(self, msg: Dict[str, Any], room: int) -> Dict[str, Any]:
        """Return *msg* with its content trimmed to fit *room* tokens.

        Iterates with a safety margin because role overhead is constant and is
        not scaled by the char-to-token ratio, so a single proportional pass can
        land slightly over. Bounded loop always terminates.
        """
        trimmed = dict(msg)
        content = str(trimmed.get("content") or "")
        for _ in range(8):
            size = self._estimator.count_message_tokens(trimmed)
            if size <= room:
                return trimmed
            if room <= 0 or len(content) <= 64:
                trimmed["content"] = "[truncated]"
                return trimmed
            keep_chars = max(64, int(len(content) * room / max(size, 1)) * 9 // 10)
            trimmed["content"] = content[:keep_chars] + "...[truncated]"
        trimmed["content"] = "[truncated]"
        return trimmed

    def _trim_block_to_fit(
        self,
        block: List[Dict[str, Any]],
        room: int,
    ) -> None:
        """Trim the latest user message inside *block* so the block fits *room* tokens."""
        user_idx = None
        for j in range(len(block) - 1, -1, -1):
            if block[j].get("role") == "user":
                user_idx = j
                break
        if user_idx is None:
            return
        block[user_idx] = self._trim_message_to_fit(block[user_idx], room)

    def _is_key_fact_message(self, msg: Dict[str, Any]) -> bool:
        """Check if a message contains key facts that must survive truncation.

        Key facts include:
        * User corrections ("use X not Y", "I said X", "you got it wrong")
        * Model names referenced in tool results
        * Explicit decisions and specifications
        """
        content = str(msg.get("content") or "")
        role = msg.get("role", "")

        if not content:
            return False

        # User corrections are always key facts
        if role == "user":
            correction_patterns = (
                r"i said",
                r"use\s+.+?\s+not\s+",
                r"you got.*?wrong",
                r"not\s+.+?\s+model",
                r"wrong.*?model",
            )
            for pat in correction_patterns:
                if re.search(pat, content, re.IGNORECASE):
                    return True

        # Tool results containing model names are key facts
        if role == "tool":
            model_patterns = (
                r"MiniCPM[-o]?\s*\d*\.?\d*",
                r"Phi-?3\s*Vision",
                r"Moondream2",
                r"Llava[-v]?\d*\.?\d*",
                r"Qwen2-VL-\d*\.?\d*",
            )
            for pat in model_patterns:
                if re.search(pat, content, re.IGNORECASE):
                    return True

        return False

    def _truncate_messages(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int,
    ) -> List[Dict[str, Any]]:
        """Emergency truncation that preserves system + the active user turn.

        Counts every message with the TokenEstimator INCLUDING tool_calls
        (single token truth), groups assistant(tool_calls) with its tool
        results into atomic blocks so no tool message is ever orphaned,
        skips oversize middle messages instead of breaking at them, and
        always keeps (trimming if necessary) the latest user turn so the
        model never loses the active question.

        Additionally, blocks containing key facts (user corrections, model
        references, decisions) are pinned so they are never dropped by
        truncation — preventing context loss where the agent forgets
        earlier parts of the conversation.
        """
        system_msg = None
        rest = list(messages)
        if rest and rest[0].get("role") == "system":
            system_msg = rest.pop(0)

        def _size(msg: Dict[str, Any]) -> int:
            return self._estimator.count_message_tokens(msg)

        # count_messages_tokens adds one conversation-level overhead on top of
        # per-message counts; budget against the net target so the final
        # estimate never overshoots.
        dummy = {"role": "user", "content": "x"}
        conv_overhead = max(
            0,
            self._estimate_messages_tokens([dummy]) - _size(dummy),
        )
        working_target = max(1, target_tokens - conv_overhead)

        # Never exceed the target even for a lone system message.
        if system_msg is not None:
            if _size(system_msg) > working_target:
                system_msg = self._trim_message_to_fit(system_msg, working_target)

        # ---- 1. Group into atomic blocks: assistant tool_calls binds its results ----
        blocks: List[List[Dict[str, Any]]] = []
        i = 0
        while i < len(rest):
            msg = rest[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                block = [msg]
                j = i + 1
                while j < len(rest) and rest[j].get("role") == "tool":
                    block.append(rest[j])
                    j += 1
                blocks.append(block)
                i = j
            else:
                blocks.append([msg])
                i += 1

        # ---- 2. Find blocks to pin: latest user turn + key-fact blocks ----
        pinned_block_idx: List[int] = []
        for b in range(len(blocks) - 1, -1, -1):
            block = blocks[b]
            # Always pin the latest user turn
            if any(m.get("role") == "user" for m in block):
                pinned_block_idx.append(b)
                break

        # Also pin any block containing key facts (user corrections, model refs)
        for b in range(len(blocks) - 1, -1, -1):
            if b in pinned_block_idx:
                continue
            if any(self._is_key_fact_message(m) for m in blocks[b]):
                pinned_block_idx.append(b)

        used = _size(system_msg) if system_msg is not None else 0
        pinned_blocks: List[List[Dict[str, Any]]] = []
        for pb_idx in sorted(pinned_block_idx):
            block = list(blocks[pb_idx])
            room = max(0, working_target - used)
            bsize = sum(_size(m) for m in block)
            if bsize > room:
                self._trim_block_to_fit(block, room)
            used += sum(_size(m) for m in block)
            pinned_blocks.append(block)

        # ---- 3. Fill the rest newest-first; skip oversize, never break ----
        keep = [False] * len(blocks)
        for pb_idx in pinned_block_idx:
            keep[pb_idx] = True
        for b in range(len(blocks) - 1, -1, -1):
            if keep[b]:
                continue
            bsize = sum(_size(m) for m in blocks[b])
            if used + bsize > working_target:
                continue
            keep[b] = True
            used += bsize

        # ---- 4. Emit in original order ----
        ordered: List[Dict[str, Any]] = []
        if system_msg is not None:
            ordered.append(system_msg)
        for b, block in enumerate(blocks):
            if b in pinned_block_idx:
                ordered.extend(pinned_blocks[pinned_block_idx.index(b)])
            elif keep[b]:
                ordered.extend(block)
        return ordered

    def _sync_back_to_session(
        self,
        session_id: str,
        current_messages: List[Dict[str, Any]],
    ) -> None:
        """Persist compacted context onto the live session (Bug 3 fix).

        Mirrors the sandbox-validated pattern: clear + extend under a
        provider try/except so a misbehaving provider can never break a turn.
        """
        if self._session_provider is None:
            return
        try:
            session = self._session_provider(session_id)
        except Exception:
            logger.debug("[ContextWindowManager] session provider failed for %s", session_id)
            return
        if session is None or not hasattr(session, "messages"):
            return
        try:
            session.messages.clear()
            session.messages.extend(current_messages)
        except Exception:
            logger.debug(
                "[ContextWindowManager] could not replace session messages for %s",
                session_id,
            )
    
    def _store_result(self, session_id: str, result: ContextManagementResult) -> None:
        """Store result in history."""
        if session_id not in self._results:
            self._results[session_id] = []
        
        self._results[session_id].append(result)
        
        # Keep last 50 results
        if len(self._results[session_id]) > 50:
            self._results[session_id] = self._results[session_id][-50:]
    
    def get_management_history(self, session_id: str) -> List[ContextManagementResult]:
        """Get context management history for session."""
        return self._results.get(session_id, [])
    
    def get_budget_status(self) -> Dict[str, Any]:
        """Get current budget status."""
        return self.budget.to_dict()
    
    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        self._results.pop(session_id, None)
        self.compression_service.clear_session(session_id)
        self.budget.reset()


# Re-export ContextManagementResult as EngineResult for public API compatibility
EngineResult = ContextManagementResult


class ContextEngine:
    """
    Unified context management for the agent.

    Single entry point for the agent's chat loop. Orchestrates:
      Stage 0: Token estimation and budget check
      Stage 1: Tool output masking (fast, no LLM)
      Stage 2: Compression via LLM summarization
      Stage 3: Emergency truncation (last resort)

    Also provides:
      - prepare_messages() — main entry point called before each LLM call
      - distill_tool_result() — per-tool-call truncation
      - assemble_prompt() — system prompt construction
      - inject_hint() / remove_hint() — message injection utilities

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
        session_provider: Optional[Callable[[str], Optional[Any]]] = None,
    ):
        self.config = config
        self.debug = debug
        self.telemetry_tracker = telemetry_tracker

        # Sub-components (all now in this module)
        from logicore.runtime.context.token_estimator import TokenEstimator
        from logicore.runtime.context.prompt_assembler import PromptAssembler
        from logicore.runtime.context.message_pipeline import MessagePipeline
        from logicore.runtime.context.tool_output_distiller import ToolOutputDistiller

        provider_name = getattr(llm_provider, "provider_name", "unknown") if llm_provider else "unknown"
        if isinstance(token_counter, TokenEstimator):
            self.token_estimator = token_counter
        elif token_counter is not None:
            self.token_estimator = TokenEstimator(token_counter)
        else:
            self.token_estimator = TokenEstimator(provider=provider_name, model=model_name)
        # Resolve the system-prompt token cap. Prefer a ratio of the actual
        # model context window; fall back to the absolute configured default.
        from logicore.runtime.context.token_estimator import get_model_context_window
        model_window = get_model_context_window(model_name, llm_provider)
        resolved_max_tokens = min(
            config.context.system_prompt_max_tokens,
            max(1, int(model_window * config.context.system_prompt_max_tokens_ratio)),
        )

        self.prompt_assembler = PromptAssembler(
            max_tokens=resolved_max_tokens,
            token_estimator=self.token_estimator,
            debug=debug,
        )
        self.message_pipeline = MessagePipeline()
        self.tool_output_distiller = ToolOutputDistiller(
            context_cfg=config.context,
            model_window=model_window,
            estimator=self.token_estimator,
        )

        # The heavy lifter: ContextWindowManager handles masking → compression → truncation
        self._window_manager = ContextWindowManager(
            config=config,
            llm_provider=llm_provider,
            model_name=model_name,
            token_counter=self.token_estimator,
            session_provider=session_provider,
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
        3. Return managed messages ready for the API

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
                print(f"{warning('[ContextEngine]')} Context management failed: {e}. Using original messages.")
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
                f"{info('[ContextEngine]')} Pipeline: {result.original_tokens} → {result.final_tokens} tokens "
                f"(saved {success(str(result.tokens_saved))}). "
                f"masked={result.masked} compressed={result.compressed} truncated={result.truncated}"
            )

        # Store result for telemetry
        self._store_result(session_id, result)

        return result, managed_messages

    def distill_tool_result(
        self,
        tool_name: str,
        result: Dict[str, Any],
        reused: bool = False,
        session_id: str = "default",
    ) -> str:
        """
        Distill a tool result for the model context.

        Called per-tool-call to enforce per-result size limits.
        """
        return self.tool_output_distiller.distill(tool_name, result, reused, session_id=session_id)

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

    def _store_result(self, session_id: str, result: EngineResult) -> None:
        """Store result in history."""
        if session_id not in self._results:
            self._results[session_id] = []
        self._results[session_id].append(result)
        # Keep last 100 results
        if len(self._results[session_id]) > 100:
            self._results[session_id] = self._results[session_id][-100:]
