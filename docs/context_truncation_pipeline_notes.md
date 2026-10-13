# Logicore Context Truncation / Compaction Pipeline — Product Owner Notes

> Based on deep codebase exploration (2026-10-09). These notes capture *how it actually works* vs *how it should work*, with bug citations.

---

## 1. High-Level Architecture: Three Overlapping Systems

There is **no single pipeline**. Three compression mechanisms run at different times and interfere with each other:

| System | File | Trigger | Mutates `session.messages`? | Purpose |
|--------|------|---------|----------------------------|---------|
| **ContextWindowManager** (read-only) | `runtime/context/manager.py:71` | Every `prepare_messages()` call — **per LLM iteration** | **No** — operates on `messages.copy()` | Non-destructive masking + LLM summarization + emergency truncation on the *request payload only* |
| **ProgressiveCompressor** (heuristic) | `agent/progressive_compressor.py` | (a) Proactive: per-iteration if tokens > 70% window (b) Reactive: on context-overflow error | **Proactive: No** (local copy); **Reactive: Yes** (writes back at `chat_orchestrator.py:1181`) | Destructive history rewrite: keeps `messages[:1]` + `preserve_recent` + LLM-free heuristic summary |
| **ToolOutputDistiller** (per-tool) | `runtime/context/tool_output_distiller.py:37` | At tool-result serialization (`Agent._serialize_tool_result_for_model`) | Yes — distills result before `session.add_message()` | Caps tool output at 6k chars / 2k preview |

Plus a fourth: **ToolOutputMaskingService** (`runtime/context/masking.py:55`) — masks old tool outputs inside the manager, but **only fires if the whole conversation already exceeds the compression threshold** (gate ordering bug).

---

## 2. Where the Request Payload Is Built

`ChatOrchestrator.run()` → per-iteration loop (`chat_orchestrator.py:418`):

```python
llm_messages = session.messages                    # full session history
_ctx_result, llm_messages = await agent.context_engine.prepare_messages(llm_messages)
# → masking → LLM compression → truncation (all on a COPY)
# → progressive compression check (local copy, or reactive write-back)
# → BEFORE_MODEL hooks
# → gateway.chat_stream(llm_messages, tools=...)
```

**Critical**: `session.messages` is **never** trimmed by the main path. Only the reactive error path writes back a compressed version.

---

## 3. Triggers & Thresholds (Defaults)

| Knob | Value | Where Used |
|------|-------|------------|
| `CONTEXT_COMPRESS_THRESHOLD` | 0.85 (ratio) | `TokenBudget.should_compress()` gate |
| `compression_threshold_tokens` | 100,000 (absolute) | `CompressionService.compress()` — **hard floor** |
| `protection_threshold_tokens` | 20,000 | Masking protection window |
| `min_prunable_tokens` | 10,000 / 30,000 (env) | Masking prune trigger |
| `ToolConfig.max_output_chars` | 12,000 | Distiller cap |
| `SYSTEM_PROMPT_MAX_TOKENS` | min(16k, 15% window) | Prompt assembler cap |
| `max_iterations` | 40 (60 in script) | Loop bound |
| `max_history_messages` | 100 | **Dead knob** — never enforced |

---

## 4. The Three Fatal Bugs (Why Context Bloats)

### Bug 1: Compaction Never Sticks (Main Path is Read-Only)
- `ContextWindowManager.manage()` operates on `messages.copy()` (`manager.py:171`).
- The compressed list is returned to `llm_messages` (local) and **dropped next iteration**.
- LLM summarization re-runs **every iteration** (up to 60×), output discarded.
- Only **reactive** compaction (on 5xx error) writes back to `session.messages` (`chat_orchestrator.py:1181`).

### Bug 2: Summarization Silently Disabled for Most Models
- Gate uses ratio: `0.85 × model_window` (e.g., 27k for 32k window).
- `CompressionService.compress()` refuses if `original_tokens < 100_000` (`compression.py:274`).
- **Result**: For any model with window < ~118k, `SKIPPED_SMALL` forever → LLM summarization never runs.
- Only masking + hard truncation remain.

### Bug 3: Retry State Reset Per Iteration = Infinite Retry Storm
- `TurnRetryState` recreated at **every iteration** (`chat_orchestrator.py:420`).
- `reset_for_new_turn()` called inside the loop (`chat_orchestrator.py:443`).
- **"Attempt 1/3" forever** — each iteration gets fresh 3 retries.
- On flaky upstream (500), agent burns all 60 iterations (~15 min) with no circuit breaker.

---

## 5. Leaks & Bloat Sources

| Source | Effect | Fix Status |
|--------|--------|------------|
| **Injected hints never cleared** (`_clear_injected_hints` key-collision bug, exception bypass) | System messages accumulate permanently | Bug 7.5 |
| **Walkthrough adds 2 messages/turn** (`_generate_walkthrough_summary`) | Never marked ephemeral, survives compaction/restore | Bug 7.10 |
| **Reactive compaction summaries lost on restore** (wrong marker text) | Post-restart, compaction context dropped | Bug 7.6 |
| **`max_history_messages=100` dead** | No trimming by message count | Bug 7.1 |
| **Tool schema tokens not in budget** (`TokenBudget.TOOLS` unused) | Deferral and context budget disjoint | Bug 7.14 |

---

## 6. Session Continuity — The Silent Amnesia

`data_gatherer.py` calls `agent.stream_run(msg)` **without `session_id`** → `session_id = f"default-{uuid4.hex[:8]}"` **every turn**.

- New `AgentSession` each turn → only system prompt survives.
- User perceives "agent forgot everything" — actually **no shared session**.
- Fix: pass fixed `session_id` (done in corrected script).

---

## 7. Trace / Debug Capability — What Exists vs What's Needed

| Mechanism | Captures | Gap |
|-----------|----------|-----|
| `Agent(debug=True)` | Count-level logs (masked/compressed/truncated flags, token counts) | No payloads |
| `storage.save_session()` | Full `session.messages` transcript (best available dump) | No per-iteration request/response |
| `StreamEmitter` | Token/tool/usage events, **previews only** | No full payloads |
| `TelemetryCollector` | Event types `CONTEXT_COMPRESSED`/`CONTEXT_TRUNCATED` | **Unwired** — only in unused `AgentRuntime` |
| `masking.py` spill files | Original tool outputs on disk (`%TEMP%/logicore_masked/`) | Outside repo, manual retrieval |

**Missing**: A toggle that dumps the exact `llm_messages` array sent to the model after masking/compression/truncation, plus the raw model response. The `SessionTracer` built for this exercise (`scripts/session_trace.py`) fills this gap using hooks.

---

## 8. Priority Fix List (Owner View)

| Priority | Fix | Impact |
|----------|-----|--------|
| P0 | Move `TurnRetryState` creation **outside** the iteration loop; share per-turn | Stops 15-min retry storms on flaky upstream |
| P0 | Make `ContextWindowManager.manage()` write back to `session.messages` (or have orchestrator persist the compressed copy) | Compaction actually reduces history |
| P0 | Align `compression_threshold_tokens` (100k) with ratio gate (0.85×window) — remove absolute floor or make it proportional | Enables LLM summarization on normal models |
| P1 | Fix `_clear_injected_hints` key collision + exception bypass | Stops hint leak bloat |
| P1 | Mark walkthrough messages ephemeral / exclude from compaction/restore | Stops +2 msg/turn bloat |
| P1 | Enforce `max_history_messages` or remove dead knob | Prevents unbounded message growth |
| P1 | Wire tool schema tokens into `TokenBudget.TOOLS` | Unified budget for deferral + context |
| P2 | Session persistence: restore compaction summaries (match marker text) | Post-restart context integrity |
| P2 | `TELEMETRY_LOG_PROMPTS` flag → actually log payloads when enabled | Debuggability |

---

## 9. Verified Working Flow (After Script Fixes)

With the corrected `data_gatherer.py`:
- `system_prompt=SYSTEM_PROMPT` wired → agent knows its role
- Fixed `session_id="leadgen-research"` → 3-turn continuity (105 messages, 61k tokens)
- `agent.set_auto_approve_all(True)` → no approval deadlock in headless mode
- Ollama `nemotron-3-ultra:cloud` → stable, no 500/429 storms
- 3-turn research completed: found "Kunal Kadakia" for AK Pipe Fittings; mapped related Shreeji entities for Shreeji Pipe Fittings LLP
- Trace captured via `SessionTracer` hooks → full replayable JSONL (298 records, 8.3 MB)

---

## 10. Key Files for Future Work

| Area | File |
|------|------|
| Context manager (read-only) | `runtime/context/manager.py` |
| LLM compression service | `runtime/context/compression.py` |
| Token budget & thresholds | `runtime/context/token_budget.py`, `runtime/context/token_estimator.py` |
| Progressive compressor (heuristic) | `agent/progressive_compressor.py` |
| Tool output distillation | `runtime/context/tool_output_distiller.py` |
| Tool output masking | `runtime/context/masking.py` |
| Orchestrator loop & retry state | `agent/chat_orchestrator.py` (lines 418-445, 528-540, 1158-1190) |
| Prompt assembly & system prompt cap | `runtime/context/prompt_assembler.py` |
| Session storage / restore | `agent/base.py` (`_restore_session_from_storage`) |
| Hook system (for tracing) | `runtime/hooks/types.py`, `runtime/hooks/system.py` |
| Custom tracer (this exercise) | `scripts/session_trace.py` |
| Corrected research script | `scripts/data_gatherer.py` |

---

> **Bottom line**: The framework has rich compaction machinery, but the main path is read-only, the summarization gate is miscalibrated, and the retry logic burns iterations. Fixing the three P0 bugs would turn the pipeline from "theatrical" to functional.