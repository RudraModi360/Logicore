"""
Prototype of the Plan 02 Engine 1 fixes (masking & compaction), living entirely
in experiments/ so the real framework stays untouched until the PO signs off.

Each class SUBCLASSES the working framework machinery and overrides only the
buggy method, so the delta is exactly the fix:

  FixedTokenBudget.should_mask_tool_outputs      -> Bug 1 enabler (independent trigger)
  FixedCompressionService.__init__               -> Bug 2 (model-aware threshold, no 100k floor)
  FixedMaskingService._create_masked_content     -> read_file() retrieval instruction
  FixedContextWindowManager.manage               -> Bug 1 (gate inversion) + Bug 3 (session sync)
  FixedContextWindowManager._sync_back_to_session-> Bug 3 persistence helper

Nothing here is imported by the framework. Wire it into a sandbox, watch the
traces, and only once it looks right do we port the delta into logicore/.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.context.manager import ContextWindowManager, ContextManagementResult
from logicore.runtime.context.compression import CompressionService, CompressionStatus
from logicore.runtime.context.masking import ToolOutputMaskingService, MASKING_INDICATOR
from logicore.runtime.context.token_budget import TokenBudget, TokenCategory
from logicore.utils.colors import warning, info, success

logger = logging.getLogger(__name__)


class FixedTokenBudget(TokenBudget):
    """Bug 1 enabler: masking trigger no longer waits for the compress gate."""

    def should_mask_tool_outputs(self) -> bool:
        """Fire tool-output masking independently of the compression gate.

        Triggers when tool outputs are heavy relative to the window (~35%,
        model-profile-aware) OR total usage approaches the compression threshold,
        so cheap masking always gets a chance before LLM summarization.
        """
        tool_result_tokens = self._usage.by_category.get(TokenCategory.TOOL_RESULTS, 0)
        tool_threshold = min(
            self.config.context.protection_threshold_tokens,
            max(1, int(self.context_window * 0.35)),
        )
        is_tool_heavy = tool_result_tokens >= tool_threshold
        approach_ratio = max(0.0, self.config.context.compression_threshold_ratio - 0.20)
        is_approaching_limit = self.get_usage_ratio() >= approach_ratio
        return is_tool_heavy or is_approaching_limit


class FixedMaskingService(ToolOutputMaskingService):
    """Placeholder tells the agent how to retrieve the offloaded data."""

    def _create_masked_content(self, original_content: str, tool_name: str, filepath: str) -> str:
        line_count = original_content.count("\n") + 1
        char_count = len(original_content)
        token_estimate = self._count_tokens(original_content)
        return (
            f"{MASKING_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Output size: {char_count} chars (~{token_estimate} tokens), {line_count} lines\n"
            f"Full output saved to: {filepath}\n"
            f"Instruction: Output was masked to preserve context window. "
            f"If you need details from this output, use read_file('{filepath}')."
        )


class FixedCompressionService(CompressionService):
    """Bug 2: model-aware threshold replaces the hardcoded 100k floor."""

    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        token_counter: Optional[Callable[[str], int]] = None,
        model_name: str = "default",
    ):
        super().__init__(config, llm_provider, token_counter)
        # Write the model-aware threshold back so the inherited compress() picks
        # it up. For a 32k window this is ~24k instead of the old absolute 100k.
        config.context.compression_threshold_tokens = config.get_compression_threshold_for_model(
            model_name
        )


class FixedContextWindowManager(ContextWindowManager):
    """Bugs 1 + 3: independent masking checkpoint + session persistence."""

    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
        session_provider: Optional[Callable[[str], Any]] = None,
    ):
        # Let the base build its machinery, then swap in the FIXED sub-services
        # and record the session hook.
        super().__init__(config, llm_provider, model_name, token_counter)
        self._session_provider = session_provider
        self.budget = FixedTokenBudget(config, model_name, self._estimator, provider=llm_provider)
        self.compression_service = FixedCompressionService(
            config, llm_provider, self._estimator, model_name=model_name,
        )
        self.masking_service = FixedMaskingService(config, self._estimator)

    async def manage(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> tuple[ContextManagementResult, List[Dict[str, Any]]]:
        result = ContextManagementResult()
        current_messages = messages.copy()

        result.original_tokens = self._estimate_messages_tokens(current_messages)
        categories = self._categorize_tokens(current_messages)
        for category, tokens in categories.items():
            self.budget.set_tokens(category, tokens)

        # CHECKPOINT 1: masking, evaluated BEFORE the compress gate (Bug 1 fix).
        if self.budget.should_mask_tool_outputs():
            masking_result, current_messages = self.masking_service.mask(
                current_messages, session_id,
            )
            if masking_result.masked_count > 0:
                result.masked = True
                result.masking_result = masking_result
                new_tokens = self._estimate_messages_tokens(current_messages)
                result.tokens_saved += result.original_tokens - new_tokens
                categories = self._categorize_tokens(current_messages)
                for category, tokens in categories.items():
                    self.budget.set_tokens(category, tokens)
                self._sync_back_to_session(session_id, current_messages)

        # CHECKPOINT 2: summarization.
        if not self.budget.should_compress():
            result.final_tokens = self._estimate_messages_tokens(current_messages)
            self._store_result(session_id, result)
            return result, current_messages

        logger.info(
            f"[FixedContextWindowManager] budget exceeded: "
            f"{result.original_tokens}/{self.budget.compression_threshold} tokens"
        )
        print(
            f"\n{warning('[FixedCtx]')} budget exceeded: "
            f"{result.original_tokens}/{self.budget.compression_threshold} tokens"
        )

        print(info("[FixedCtx]") + " running compression...")
        compression_result = await self.compression_service.compress(current_messages, session_id)

        if compression_result.status == CompressionStatus.SUCCESS:
            current_messages = self.compression_service.build_compressed_messages(
                current_messages, compression_result,
            )
            result.compressed = True
            result.compression_result = compression_result
            result.tokens_saved += compression_result.tokens_saved
            print(
                f"{success('[FixedCtx]')} compression complete: "
                f"{result.original_tokens} → {self._estimate_messages_tokens(current_messages)} tokens"
            )
        else:
            print(
                f"{warning('[FixedCtx]')} compression {compression_result.status.value}: "
                f"{compression_result.error or 'unknown reason'}"
            )

        # Stage 3: emergency truncation if still over budget
        current_tokens = self._estimate_messages_tokens(current_messages)
        target_tokens = self.budget.get_compression_target()
        if current_tokens > target_tokens * 1.5:
            current_messages = self._truncate_messages(current_messages, target_tokens)
            result.truncated = True

        result.final_tokens = self._estimate_messages_tokens(current_messages)
        result.tokens_saved = result.original_tokens - result.final_tokens

        # Bug 3 fix: persist compacted history back onto the live session.
        if result.any_action_taken:
            self._sync_back_to_session(session_id, current_messages)

        self.budget.record_snapshot()
        self._store_result(session_id, result)
        return result, current_messages

    def _sync_back_to_session(self, session_id: str, current_messages: List[Dict[str, Any]]) -> None:
        if not self._session_provider:
            return
        try:
            session = self._session_provider(session_id)
        except Exception:
            return
        if session is None or not hasattr(session, "messages"):
            return
        session.messages.clear()
        session.messages.extend(current_messages)
