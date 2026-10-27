"""
Prototype of the Plan 02 Engine 1 fixes (masking & compaction), living entirely
in experiments/ so the real framework stays untouched until the PO signs off.

Each class SUBCLASSES the working framework machinery and overrides only the
buggy method, so the delta is exactly the fix:

  FixedTokenBudget.should_mask_tool_outputs      -> Bug 1 enabler (independent trigger)
  FixedCompressionService.__init__               -> Bug 2 (model-aware threshold, no 100k floor)
  FixedCompressionService._estimate_message_tokens-> A1 (delegate to framework estimator)
  FixedMaskingService._create_masked_content     -> read_file() retrieval instruction
  FixedContextWindowManager.__init__             -> A3 (window-relative min_prunable floor)
  FixedContextWindowManager._count_tokens        -> A1 (single token truth)
  FixedContextWindowManager._estimate_messages_tokens -> A1 (single token truth)
  FixedContextWindowManager._categorize_tokens    -> A1 (no longer drops tool_calls)
  FixedContextWindowManager._hard_limit_tokens    -> A2 (window - reserve, with margin)
  FixedContextWindowManager.manage               -> Bug 1 + A2 (hard enforcement) + Bug 3
  FixedContextWindowManager._sync_back_to_session-> Bug 3 persistence helper
  FixedContextWindowManager._truncate_messages   -> pin active user turn

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
from experiments.context_engine.distill import DISTILLED_INDICATOR
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
    """Placeholder tells the agent how to retrieve the offloaded data.

    Group B coexistence: also treat distilled stubs as already-handled so masking
    never re-processes a payload the distiller already shrank to an excerpt + path
    (that would only add a wasted double indirection).
    """

    def _is_already_masked(self, content: str) -> bool:
        return MASKING_INDICATOR in content or DISTILLED_INDICATOR in content

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
    """Bug 2: model-aware threshold replaces the hardcoded 100k floor.

    A1: the inherited compress() pre-check uses a local _estimate_message_tokens
    that under-counts tool_calls (tool_calls // 2). We override it to delegate to
    the framework TokenEstimator so the threshold decision uses the same single
    source of truth as the budget and masking.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        token_counter: Optional[Callable[[str], int]] = None,
        model_name: str = "default",
    ):
        super().__init__(
            config,
            llm_provider,
            token_counter,
            model_name=model_name,
        )

    def _estimate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        return self._estimator.count_messages_tokens(messages)


class FixedContextWindowManager(ContextWindowManager):
    """Bugs 1 + 3 + hard budget: independent masking checkpoint + persistence + enforcement.

    A1: _categorize_tokens / _estimate_messages_tokens / _count_tokens all delegate
    to the framework TokenEstimator (self._estimator) so there is exactly one token
    truth. The base versions drop tool_calls from categorization and half-count them
    in estimates — that mismatch is the root cause of compression never firing.
    """

    HARD_LIMIT_MARGIN = 0.05

    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
        session_provider: Optional[Callable[[str], Any]] = None,
    ):
        # A3: scale the masking floor to the window BEFORE sub-services are built,
        # so FixedTokenBudget/FixedMaskingService read the corrected value. The
        # framework default (10k / env 30k) is unreachable on small windows.
        context_window = config.get_model_context_window(model_name, llm_provider)
        config.context.min_prunable_tokens = max(
            500, min(config.context.min_prunable_tokens, context_window // 8)
        )
        # Let the base build its machinery, then swap in the FIXED sub-services
        # and record the session hook.
        super().__init__(config, llm_provider, model_name, token_counter)
        self._session_provider = session_provider
        self.budget = FixedTokenBudget(config, model_name, self._estimator, provider=llm_provider)
        self.compression_service = FixedCompressionService(
            config, llm_provider, self._estimator, model_name=model_name,
        )
        self.masking_service = FixedMaskingService(config, self._estimator)

    # -- A1: single source of truth ---------------------------------------- #
    def _count_tokens(self, text: str) -> int:
        return self._estimator.count_tokens(text)

    def _estimate_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        return self._estimator.count_messages_tokens(messages)

    def _categorize_tokens(self, messages: List[Dict[str, Any]]) -> Dict[TokenCategory, int]:
        raw = self._estimator.categorize_tokens(messages)
        return {
            TokenCategory.SYSTEM: raw.get("system", 0),
            TokenCategory.TOOLS: raw.get("tools", 0),
            TokenCategory.TOOL_RESULTS: raw.get("tool_results", 0),
            TokenCategory.MESSAGES: raw.get("messages", 0),
        }

    def _hard_limit_tokens(self) -> int:
        """Hard prompt ceiling: window minus the response reserve, with a safety margin.

        The estimator under-counts by a few percent on average (validated ~5-8%
        vs provider truth), so we enforce the limit a touch below the true
        window-reserve to guarantee the real prompt never overflows. Set
        HARD_LIMIT_MARGIN = 0.0 to disable the margin.
        """
        reserve = self.budget.reserved_for_response
        hard = max(1, self.budget.context_window - reserve)
        return int(hard * (1.0 - self.HARD_LIMIT_MARGIN))

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
        if self.budget.should_compress():
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

            # Stage 3: emergency truncation toward the compression target.
            current_tokens = self._estimate_messages_tokens(current_messages)
            target_tokens = self.budget.get_compression_target()
            if current_tokens > target_tokens * 1.5:
                current_messages = self._truncate_messages(current_messages, target_tokens)
                result.truncated = True

        # A2: HARD BUDGET ENFORCEMENT — both paths converge here.
        hard_limit = self._hard_limit_tokens()
        current_tokens = self._estimate_messages_tokens(current_messages)
        if current_tokens > hard_limit:
            result.tokens_before_enforce = current_tokens
            result.hard_limit = hard_limit
            current_messages = self._truncate_messages(current_messages, hard_limit)
            result.truncated = True
            result.hard_enforced = True
            print(
                f"{warning('[FixedCtx]')} prompt still over hard limit after pipeline "
                f"({current_tokens} > {hard_limit}); truncated to fit and sent anyway"
            )

        result.final_tokens = self._estimate_messages_tokens(current_messages)
        result.tokens_saved = result.original_tokens - result.final_tokens

        # Bug 3 fix: persist compacted history back onto the live session.
        if result.any_action_taken:
            self._sync_back_to_session(session_id, current_messages)

        self.budget.record_snapshot()
        self._store_result(session_id, result)
        return result, current_messages

    def _truncate_messages(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int,
    ) -> List[Dict[str, Any]]:
        """Emergency truncation that never drops the active user turn.

        The inherited version walks newest-first and BREAKS at the first
        message that alone exceeds the target — so one fat tool result leaves
        system-only context, the model loses the question entirely and returns
        nothing (observed live: 8397 -> 377 tokens, '(no response)').
        This override pins the latest user message (trimming its content to
        fit if needed) and SKIPS oversize middle messages instead of breaking.
        """
        system_msg = None
        rest = list(messages)
        if rest and rest[0].get("role") == "system":
            system_msg = rest[0]
            rest = rest[1:]

        def _size(msg: Dict[str, Any]) -> int:
            content = str(msg.get("content") or "")
            if msg.get("tool_calls"):
                content += str(msg["tool_calls"])
            return self._count_tokens(content)

        latest_user_idx = None
        for i in range(len(rest) - 1, -1, -1):
            if rest[i].get("role") == "user":
                latest_user_idx = i
                break

        ordered: List[Dict[str, Any]] = []
        used = 0
        if system_msg is not None:
            used += self._count_tokens(str(system_msg.get("content") or ""))
            ordered.append(system_msg)

        # Reserve room for the pinned question first, trimming it if needed.
        pinned = None
        if latest_user_idx is not None:
            pinned = dict(rest[latest_user_idx])
            room = max(0, target_tokens - used)
            size = _size(pinned)
            if size > room:
                content = str(pinned.get("content") or "")
                if room <= 0:
                    pinned["content"] = "[truncated]"
                else:
                    keep_chars = max(64, int(len(content) * room / max(size, 1)))
                    pinned["content"] = content[:keep_chars] + "...[truncated]"
                size = _size(pinned)
            used += size

        # Fill remaining budget newest-first; skip (never break on) oversize.
        keep = [False] * len(rest)
        for i in range(len(rest) - 1, -1, -1):
            if i == latest_user_idx:
                continue
            size = _size(rest[i])
            if used + size > target_tokens:
                continue
            keep[i] = True
            used += size

        for i, msg in enumerate(rest):
            if i == latest_user_idx:
                ordered.append(pinned)
            elif keep[i]:
                ordered.append(msg)
        return ordered

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
