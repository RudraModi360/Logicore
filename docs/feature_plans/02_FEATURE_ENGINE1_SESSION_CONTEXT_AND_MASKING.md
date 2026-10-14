# Feature Plan 02: Engine 1 Session Context Fixes — Masking & Compaction (Part B)

## Overview & Objective
Fix the critical logic bugs in **Engine 1 (ContextWindowManager, ToolOutputMaskingService, CompressionService)**. 

Prior to this fix, tool output masking (disk offloading) was completely unreachable due to an inverted gate check, and multi-turn LLM summarization was permanently blocked for models with context < 118k tokens due to a hardcoded 100,000-token floor.

---

## Status & Tracking
- **Status:** `SANDBOX PASS — AWAITING PO SIGN-OFF`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - [`logicore/runtime/context/manager.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/manager.py) [MODIFY]
  - [`logicore/runtime/context/compression.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/compression.py) [MODIFY]
  - [`logicore/runtime/context/token_budget.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/token_budget.py) [MODIFY]
  - [`logicore/runtime/context/masking.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/masking.py) [MODIFY]
  - `tests/test_context_manager.py` [MODIFY / ADD]

---

## The Three Root Bugs & Line Citations

### Bug 1: Gate Inversion (Masking Blocked by Compression Gate)
- **Location:** [`logicore/runtime/context/manager.py:200`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/manager.py#L200)
- **Problem:** `manager.manage()` evaluates `if not self.budget.should_compress(): return messages` before calling masking.
- `should_compress()` requires total tokens to exceed `compression_threshold` (`0.85 * window` = 102.4k tokens).
- Therefore, even if 30,000 tokens of tool output exist, masking **never executes** because total tokens are under 102.4k.

### Bug 2: The 100,000-Token Hard Floor
- **Location:** [`logicore/runtime/context/compression.py:276`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/compression.py#L276)
- **Problem:** `if original_tokens < threshold (100,000): return CompressionResult(status=SKIPPED_SMALL)`
- For any model with a 16k, 32k, or 64k window, summarization immediately aborts and falls straight into destructive emergency truncation.

### Bug 3: Read-Only Main Path (Compaction Never Persists)
- **Location:** [`logicore/runtime/context/manager.py:171`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/manager.py#L171)
- **Problem:** `manager.manage()` modifies a local `messages.copy()`. It returns this copy to the orchestrator for that single request, but **never mutates `session.messages`**. The next turn reloads the full bloated history from scratch.

---

## Detailed Implementation Diff Plan

### 1. [`logicore/runtime/context/manager.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/manager.py)
**Diff in `manage()` method (around lines 198–245):**

```python
<<<<
        # Check if management needed
        if not self.budget.should_compress():
            result.final_tokens = result.original_tokens
            return result, current_messages
        
        # Stage 1: Tool output masking (fast, doesn't need LLM)
        if self.budget.should_mask_tool_outputs():
            masking_result, current_messages = self.masking_service.mask(
                current_messages,
                session_id,
            )
            
            if masking_result.masked_count > 0:
                result.masked = True
                result.masking_result = masking_result
                new_tokens = self._estimate_messages_tokens(current_messages)
                result.tokens_saved += result.original_tokens - new_tokens
====
        # CHECKPOINT 1: Tool output masking (fast, doesn't need LLM)
        # Evaluated independently at 60% window or when prunable tool outputs accumulate
        if self.budget.should_mask_tool_outputs():
            masking_result, current_messages = self.masking_service.mask(
                current_messages,
                session_id,
            )
            if masking_result.masked_count > 0:
                result.masked = True
                result.masking_result = masking_result
                new_tokens = self._estimate_messages_tokens(current_messages)
                result.tokens_saved += result.original_tokens - new_tokens
                # Recount budget categories
                categories = self._categorize_tokens(current_messages)
                for category, tokens in categories.items():
                    self.budget.set_tokens(category, tokens)
                # PERSIST TO SESSION: Sync changes back to session.messages
                self._sync_back_to_session(session_id, current_messages)

        # CHECKPOINT 2: Multi-turn LLM summarization (evaluated at 75-85% threshold)
        if not self.budget.should_compress():
            result.final_tokens = self._estimate_messages_tokens(current_messages)
            self._store_result(session_id, result)
            return result, current_messages
>>>>
```

Add helper method `_sync_back_to_session(self, session_id: str, current_messages: List[Dict[str, Any]])`:
```python
    def _sync_back_to_session(self, session_id: str, current_messages: List[Dict[str, Any]]) -> None:
        """Sync masked or compressed messages back to the active session object."""
        if hasattr(self, "_agent") and self._agent:
            session = self._agent.get_session(session_id)
            if session:
                # Update session messages in place to preserve pointers
                session.messages.clear()
                session.messages.extend(current_messages)
```

---

### 2. [`logicore/runtime/context/compression.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/compression.py)
**Diff in `compress()` method (around lines 272–285):**

```python
<<<<
        original_tokens = self._estimate_message_tokens(messages)
        threshold = self.config.context.compression_threshold_tokens
        
        if original_tokens < threshold:
            return CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=0,
                messages_preserved=len(messages),
            )
====
        original_tokens = self._estimate_message_tokens(messages)
        # Dynamic threshold: proportional to model context window (no hardcoded 100k floor)
        model_window = getattr(self.config.context, "max_context_tokens", 64000)
        threshold = min(
            self.config.context.compression_threshold_tokens,
            int(model_window * self.config.context.compression_threshold_ratio)
        )
        
        if original_tokens < threshold:
            return CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=0,
                messages_preserved=len(messages),
            )
>>>>
```

---

### 3. [`logicore/runtime/context/token_budget.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/token_budget.py)
**Diff in `should_mask_tool_outputs()` (around lines 181–185):**

```python
<<<<
    def should_mask_tool_outputs(self) -> bool:
        """Check if tool output masking should be triggered."""
        tool_result_tokens = self._usage.by_category.get(TokenCategory.TOOL_RESULTS, 0)
        return tool_result_tokens >= self.config.context.protection_threshold_tokens
====
    def should_mask_tool_outputs(self) -> bool:
        """Check if tool output masking should be triggered (Checkpoint 1)."""
        tool_result_tokens = self._usage.by_category.get(TokenCategory.TOOL_RESULTS, 0)
        # Trigger if tool outputs exceed 35% of total window, OR total tokens cross 60%
        threshold = min(self.config.context.protection_threshold_tokens, int(self.context_window * 0.35))
        is_tool_heavy = tool_result_tokens >= threshold
        is_approaching_limit = self.get_usage_ratio() >= (self.config.context.compression_threshold_ratio - 0.20)
        return is_tool_heavy or is_approaching_limit
>>>>
```

---

### 4. [`logicore/runtime/context/masking.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/masking.py)
Update `_create_masked_content()` ([`masking.py:192`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/masking.py#L192)) to provide clear instructions so the agent knows it can retrieve the data:

```python
    def _create_masked_content(self, original_content: str, tool_name: str, filepath: str) -> str:
        line_count = original_content.count("\n") + 1
        char_count = len(original_content)
        token_estimate = self._count_tokens(original_content)
        return (
            f"{MASKING_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Output size: {char_count} chars (~{token_estimate} tokens), {line_count} lines\n"
            f"Full output saved to: {filepath}\n"
            f"Instruction: Output was masked to preserve context window. If you need details from this output, use read_file('{filepath}')."
        )
```

---

## Verification Plan

### Automated Test: `tests/test_context_manager.py`
Add regression tests:
1. `test_masking_triggers_before_compression()`: Verify that when messages contain 15k tokens of tool results in a 32k window, masking runs even though total tokens < 85% compression threshold.
2. `test_compression_triggers_on_small_window()`: Verify that on a simulated 32k window, summarization executes at 25k tokens without returning `SKIPPED_SMALL`.
3. `test_masking_persists_to_session()`: Verify that `session.messages` contains the `[OUTPUT_MASKED]` string on the subsequent turn.

### Verification Command
```powershell
pytest tests/test_context_manager.py -k "masking or compression" -v
```

---

## Acceptance Criteria
- [ ] Tool output masking triggers and offloads tool JSON to `%TEMP%/logicore_masked/` when tool outputs accumulate.
- [ ] Placeholder containing file path and `read_file(...)` instruction is inserted into `session.messages`.
- [ ] Summarization runs on windows < 100k tokens without `SKIPPED_SMALL`.
- [ ] Changes to `current_messages` are synced back to `session.messages`.

---

## Prototype Status (experiment-first — NOT yet in framework)

Per the agreed workflow, the fixes are prototyped **entirely in `experiments/`**
first. The real framework (`logicore/`) is untouched until the PO signs off.

- **Prototype engine:** `experiments/context_engine/fixed.py` — subclasses the
  working framework machinery and overrides only the buggy methods, so the delta
  is exactly the fix:
  - `FixedTokenBudget.should_mask_tool_outputs` — Bug 1 enabler (independent trigger)
  - `FixedCompressionService.__init__` — Bug 2 (model-aware threshold, no 100k floor)
  - `FixedMaskingService._create_masked_content` — `read_file()` retrieval instruction
  - `FixedContextWindowManager.manage` — Bug 1 (gate inversion) + Bug 3 (session sync)
- **Live demo on the real prototype agent:** `python experiments/plan02_agent_demo.py`
  — plugs the context engine into `experiments/prototype_agent.py` (real tools
  bash/read_file/create_file/edit_file/web_search, real ModelClient, real loop) by
  wrapping `ModelClient.chat` so masking/compaction run before every LLM call and
  persist back into the live session. Compare with `--no-context` for the baseline.

  Example (small window so masking triggers on one big read):
  ```
  python experiments/plan02_agent_demo.py --model ollama/gemma4:cloud \
      --context-length 8192 --model-tier small \
      --turns "read <some big file> and report the char count"
  ```
  With `--context`: `[ctx] 5580 -> 354 tokens (saved 5226) masked=1`, and the agent
  re-reads the file on the next turn via the `read_file('<path>')` instruction.
  With `--no-context`: session stays ~5244 tokens (no masking).

**Next step (blocked on PO):** once the live demo looks right, port the delta from
`experiments/context_engine/fixed.py` into the framework classes
(`manager.py`, `compression.py`, `token_budget.py`, `masking.py`) and wire
`session_provider=self.get_session` in `agent/base.py`.
