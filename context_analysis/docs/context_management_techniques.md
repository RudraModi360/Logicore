# Context Management Techniques - Documentation

## Three Context Management Systems in logicore

### 1. ContextWindowManager
**File**: `logicore/runtime/context/manager.py`

**Purpose**: Per-request, non-destructive context preprocessing before each LLM call. Operates on a copy and returns a new list.

**Stages** (executed sequentially):

- **Stage 0 Budget Gate** (line ~200):
  ```python
  if not self.budget.should_compress():
      return messages  # return early if below threshold
  ```
  Checks `token_budget.py` ratio: `0.85 × model_window`

- **Stage 1 Masking** (line ~215-236):
  - Protects newest 20,000 tokens (`protection_threshold_tokens`)
  - Masks older tool outputs to `[OUTPUT_MASKED]`
  - Preserves `messages[:1]` + recent messages

- **Stage 2 LLM Compression** (line ~236-265):
  - Attempts summarization via `asyncio.wait_for(self.llm_provider.chat(...), timeout=120)`
  - **HARD FLOOR**: skips if `original_tokens < 100_000`
  - **Critical Bug**: For models with window < ~118k, `0.85 × window < 100_000`, so summarization is **permanently disabled**
  - Falls straight to hard truncation only

- **Stage 3 Emergency Truncation** (line ~266-276):
  - `_truncate_messages` binary-searches cut point
  - Snaps to `"\n## "` boundary
  - Always executes if Stage 2 was skipped

**Token Budget System** (`token_budget.py:177`):
- Ratio: `0.85 × model_window` enables compression gate
- Absolute floor: `100_000` tokens disables summarization
- **These two thresholds are incompatible** — one enables, one disables summarization for most models

---

### 2. ProgressiveCompressor
**File**: `logicore/agent/progressive_compressor.py`

**Purpose**: Heuristic, destructive rewrite of `session.messages` (local copy only).

**Call Sites** in `chat_orchestrator.py`:

- **Proactive** (line ~458-483):
  ```python
  # Inserts boundary into local llm_messages ONLY
  # NOT added to session.messages, NOT added to injected_hints
  # LOST when next iteration runs
  ```

- **Reactive** (line ~1158-1190):
  ```python
  # On context overflow error, writes to session.messages at line 1181
  # But NOT added to injected_hints → persists forever across turns
  ```

**Key Bugs**:

1. **Proactive boundary lost**: Inserted into local `llm_messages` only → destroyed when next iteration runs. Never persists across turns.

2. **Reactive boundary persists without cleanup**: Written to `session.messages` but **not added to `injected_hints`** → accumulates indefinitely across turns. Each turn adds another boundary message.

3. **`reset_for_new_turn()` called inside max_iterations loop** (line ~420):
   - Creates 3 attempts per LLM iteration, not per user turn
   - `TurnRetryState` recreated each iteration → circuit breaker never persists across iterations

4. **No LLM summarization**: Despite class name, uses keyword heuristics only (classify by "?" → pending asks, etc.). Calls no LLM.

---

### 3. ToolOutputDistiller
**File**: `logicore/runtime/context/tool_output_distiller.py`

**Purpose**: Per-tool-result distillation at insert time (before `session.add_message()`).

**Configuration** (`runtime/config.py`):

- `DEFAULT_MAX_CHARS = 6000`
- `DEFAULT_PREVIEW_CHARS = 2000`
- `ToolConfig.max_output_chars = 12000` (line 168)
- `ToolConfig.max_output_tokens = 3000` (line 171) — **never read anywhere**

**How It Works**:

- Distills tool results **before** `session.add_message()` (`base.py:1214-1215`)
- Calls `context_engine.distill_tool_result` (`manager.py:491`)
- Truncates to 6k chars / 2k preview by default

**Dead Code**:

- `reused_cached_result` field (`tool_output_distiller.py`) — **never set** from `ToolExecutor.execute`
- `ToolConfig.max_output_tokens = 3000` — **never read anywhere**
- The distiller is applied per-result, but no cumulative context management across results

---

## Summary of bugs & quick fixes

| System | Bug | Fix Priority |
|--------|-----|--------------|
| ContextWindowManager | Stage 2 summarization permanently disabled for models < 118k context (ratio/floor conflict) | P1 — merge ratio/floor logic |
| ProgressiveCompressor | Proactive boundary lost (local copy only) | P2 — persist to session.messages or injected_hints |
| ProgressiveCompressor | Reactive boundary persists without injected_hints cleanup | P2 — add to injected_hints on write |
| ToolOutputDistiller | `max_output_tokens = 3000` dead code; `reused_cached_result` never set | P3 — remove or wire up |
| All | No query dedup across turns → redundant web_search calls | P4 — add in-session cache |