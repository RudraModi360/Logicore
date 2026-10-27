# Feature Plan 02: Engine 1 Session Context Fixes — Masking & Compaction (Part B)

## Overview & Objective
Fix the critical logic bugs in **Engine 1 (ContextWindowManager, ToolOutputMaskingService, CompressionService)**. 

Prior to this fix, tool output masking (disk offloading) was completely unreachable due to an inverted gate check, and multi-turn LLM summarization was permanently blocked for models with context < 118k tokens due to a hardcoded 100,000-token floor.

---

## Status & Tracking
- **Status:** `PHASE-1 HYGIENE SHIPPED · PHASE 2–4 HARDENING IN PROGRESS (PLAN 02B)`
- **Owner:** Developer Agent Team
- **Reviewer:** Product Owner
- **Target Files:**
  - [`logicore/runtime/context/manager.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/manager.py) [MODIFIED — A1 only; Phase 3 in 02B]
  - [`logicore/runtime/context/compression.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/compression.py) [MODIFIED — A1 only; Phase 2 in 02B]
  - [`logicore/runtime/context/token_budget.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/token_budget.py) [MODIFIED — Phase 1]
  - [`logicore/runtime/context/masking.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/masking.py) [MODIFIED — Phase 1]
  - [`logicore/runtime/context/tool_output_distiller.py`](file:///c:/Users/rudra/Desktop/Scratchy/logicore/runtime/context/tool_output_distiller.py) [MODIFY — Phase 4, Group B merge]
  - `experiments/context_engine/distill.py` [EXPERIMENT — Group B; retires after Phase 4 port]
  - `tests/test_context_manager.py` [MODIFY / ADD]

### Progress
- **Group A:** A1 token-estimator delegation **ported to `logicore/`**
  (`manager.py::_estimate_messages_tokens`, `manager.py::_categorize_tokens`,
  `compression.py::_estimate_message_tokens` — uncommitted working tree).
  A2 hard limit, A3 window-relative `min_prunable`, A4 fresh compression client,
  A5 `context_manage` telemetry, Bug 1 gate inversion, Bug 3 session sync: **NOT**
  ported yet — covered by sibling plan `02B_FRAMEWORK_HARDENING_AND_HARD_TRUNCATION.md`.
- **Group B (tool-output distillation at insertion):** experiment done + all gates PASS.
  Port target merged into Phase 4 of plan 02B: offload-only design PO-approved,
  merged into existing `tool_output_distiller.py` (no parallel module).
- **Group C (compaction quality / summary):** not started.
- **pytest:** baseline (7 pre-existing failures).

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

---

## Group B: Tool-Output Distillation at Insertion (experiment — awaiting sign-off)

**Premise:** masking/compression (Group A) are *reactive* — they wait for a bulky
output to already sit in the session, then reclaim space under budget pressure.
Distillation is *preventive* — it runs the instant a tool returns, so the bulky
payload never enters the context window at all. Group B shrinks how often Group A
has to fire; the two are complementary, not competing.

### Design
- **New:** `experiments/context_engine/distill.py`
  - `ToolOutputDistiller` — if a tool result exceeds a threshold, the full output
    is saved to `temp/<session>/` and replaced with a ~200-char excerpt + path +
    `read_file('<path>')` instruction (same retrieval story as masking).
  - `DISTILL_EXEMPT_TOOLS` — `read_file` plus the interactive tools
    (`ask_user`/`human_feedback`/…). **Why:** our own stubs tell the agent to use
    `read_file` to retrieve full content; distilling `read_file`'s result would
    make the stub self-defeating (seen live: agent re-read, got another stub,
    gave up). Exempting it fixed the retrieval path — agent then retrieved the
    real value (69,082,000) from an Insee page.
  - `wrap_tool_impls()` — wraps `TOOL_IMPLS` at the tool boundary (the demo's
    existing hook point), so `run_turn` only ever sees the distilled string.
  - Threshold scales with the window (`max(500, window // 10)`) — same philosophy
    as A3's `min_prunable` floor, not a fixed absolute.
- **Wiring:** `experiments/plan02_agent_demo.py` — `--distill/--no-distill`
  (default ON), per-turn `[distill] checked/distilled/tokens_saved` report.
- **Coexistence fix (prototype, for the port):** `FixedMaskingService` now
  overrides `_is_already_masked` to treat `[OUTPUT_DISTILLED]` as handled too —
  otherwise masking would re-mask an already-distilled stub (wasted double
  indirection).

### Validation Gates (all PASS)
| Gate | Result |
|---|---|
| Replay (original 15k overflow trace) | 3 over-threshold `web_search` payloads distilled 8495→4247 tok (under the 10687 hard limit alone); exempt + idempotency checks pass |
| Coexistence | masking recognizes `[OUTPUT_DISTILLED]`, re-masks 0, no double indirection |
| Live (real agent, 4-turn, 8192 window) | distiller fired 4×; peak single-request 4810 ≤ hard_limit 5836 ≤ window 8192; `read_file` passes through intact; retrieval succeeds |
| pytest | baseline (7 pre-existing failures; the 8th was the known-flaky perf test, passes in isolation) |

**Blocked on PO:** sign-off to port Group B (`distill.py` + `_is_already_masked`
override + demo wiring pattern) into `logicore/`.
