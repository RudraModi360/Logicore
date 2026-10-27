# Feature Plan 02E: Active Turn Grace Period & Lifecycle Aging Decay

> **Target Scope:** Experimental Sandbox Prototype (`Logicore_Agent_Testing/test_02_episodic_context_pipeline.py`)  
> **Status:** READY FOR IMPLEMENTATION  
> **Purpose:** Solve the tool starvation paradox where aggressive ingestion distillation (1,200 chars) blinds the agent during active code/data inspection. Replaces static choking with dynamic headroom allocation for the active turn and deferred distillation on aging turns.

---

## 1. The Core Architecture Principle

```text
[Tool Returns Output]
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. INGESTION SAFETY NET (Physical Window Protection Only)   │
│    - Does single output exceed physical context window?     │
│    - Hard Cap = (Window - System Prompt - Reserved Response)│
│    - Example (12k window): Cap = ~8,000 tokens (32k chars). │
│    - 20k-char code files pass 100% UNTOUCHED into prompt.   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ACTIVE TURN: FULL FIDELITY GRACE PERIOD                  │
│    - For the CURRENT turn, tool output remains 100% intact. │
│    - Agent reads entire file, analyzes, and answers user    │
│      without missing code or hallucinating.                 │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
[Turn Completes: Assistant Response Generated]
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. AGING DECAY (Deferred Distillation & Pre-Masking)        │
│    - Turn N-1 and older are now "History".                  │
│    - The assistant has already synthesized the findings.    │
│    - Older turns decay into Micro-Digests or spill to disk. │
│    - Keeps context lean for future turns.                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Implementation Steps for `test_02_episodic_context_pipeline.py`

### Step 1: Replace Static `max_terminal_chars` with Dynamic Active Headroom
In `MutationAwarePreCleaner`:
Instead of defaulting to a static `max_terminal_chars = 1200` (which only allows ~300 tokens):

```python
def compute_active_tool_budget(context_window_tokens: int, current_tokens: int) -> int:
    """
    Dynamically computes the character budget for the ACTIVE turn.
    Ensures active tool output gets maximum possible headroom without
    exceeding the model's physical window.
    """
    # Reserve response tokens (e.g. 2,048) and system buffer (e.g. 1,000)
    safety_reserve = 3000
    available_tokens = max(1000, context_window_tokens - current_tokens - safety_reserve)
    
    # 1 token ~= 4 chars; allow up to 40% of context window for active tools
    elastic_char_cap = int(min(available_tokens, context_window_tokens * 0.40) * 4)
    return max(8000, elastic_char_cap)  # Minimum 8,000 chars (~2,000 tokens)
```

---

### Step 2: Implement the Grace Period & Aging Decay in `run()`
In `MutationAwarePreCleaner.run()`:

1. **Active Turn (Latest turn where user asked and agent just executed tools):**
   - Use `active_char_cap` (e.g. 20,000 to 32,000 chars).
   - If output fits in headroom, **leave it 100% untouched**.
   - Spilling to disk occurs ONLY if the output is so massive that it would trigger an immediate HTTP 400 Context Overflow.
2. **Aging Turns (Historical turns $> 1$ turn old):**
   - Once the turn is completed, the tool output is no longer the active subject.
   - Now apply **Micro-Digest transformation** or the compact 1,200-char cap.

```python
# Inside transform_payloads loop:
is_active_turn = (idx >= active_turn_start_idx)

if is_active_turn:
    # 100% Full Fidelity Grace Period for current turn
    active_cap = self.compute_active_tool_budget(self.context_window, current_tokens)
    if len(record["content"]) > active_cap:
        cleaned[idx]["content"] = self._cap_terminal(record, record["content"], limit=active_cap)
    else:
        # Pass 100% untouched!
        cleaned[idx]["content"] = record["content"]
else:
    # Aging turn: safe to decay into Micro-Digest
    cleaned[idx]["content"] = self._micro_digest(record)
```

---

### Step 3: Wire Context Window Parameter to PreCleaner
Ensure `MutationAwarePreCleaner` receives `profile.context_length` from `ModelProfile` at initialization so its headroom calculations are always proportional to the active model tier:
- 8k window $\rightarrow$ Active cap: ~12,000 chars.
- 12k window $\rightarrow$ Active cap: ~20,000 chars (passes the 20,015 char pipeline file completely!).
- 32k window $\rightarrow$ Active cap: ~45,000 chars.
- 128k+ window $\rightarrow$ Active cap: ~100,000 chars.

---

## 3. Verification Test Scenario

1. Run interactive session on 12k context:
   ```powershell
   python .\test_02_episodic_context_pipeline.py --model gemma4:cloud --provider ollama --context-length 12000
   ```
2. Ask:
   ```text
   You> Study C:\Users\rudra\Desktop\Logicore_Agent_Testing\test_02_episodic_context_pipeline.py and list the main components
   ```
3. **Expected Result**:
   - `read_file` returns 20,015 chars.
   - Pre-cleaner logs: `[pre-clean] Active turn grace period: preserved 20,015 chars in full fidelity.`
   - Model reads the full file and gives an accurate, detailed critique without hallucinating.
   - On Turn 2, Turn 1's tool output decays into a compact Micro-Digest before compression triggers.
