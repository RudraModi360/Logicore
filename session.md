# Context Management & Deduplication Engineering Debate Ledger

> **Document:** `session.md`  
> **Session Type:** Architectural Review & Engineering Debate  
> **Topic:** Agent Context Management, Deduplication, and Tool Output Distillation  
> **Purpose:** Hand off the complete context, trade-offs, empirical trace observations, and debate items to the engineering team for discussion and input collection.

---

## 1. Executive Summary & Objective

### Objective
Design and implement a generalized, evidence-preserving, and non-degrading context management architecture for the Logicore Agent framework. The system must operate reliably across diverse model tiers (from local 8k/12k models like Ollama `gemma4:cloud` up to 128k+ cloud models), avoiding context overflow crashes while ensuring the agent never starves of the factual information it needs to reason accurately.

### The Core Architectural Dilemma (The Paradox)
> **"If the context manager chokes tool outputs to save tokens before the agent can read them, the agent’s brain is starved of facts and it starts hallucinating. But if the context manager allows tools to dump unrestricted raw text into context, small models immediately crash with HTTP 400 Context Overflow."**

---

## 2. Important Context & Operational Parameters

- **Primary Test Environment:** `Logicore_Agent_Testing/test_02_episodic_context_pipeline.py`
- **Active Model Profile in Stress Tests:** `gemma4:cloud` via Ollama
- **Context Window Tested:** 12,000 tokens (Standard tier)
- **Key Empirical Trace Reference:** `debug/traces/plan02/episodic_stress_20261026-125733.json`
- **Key Target Scenarios:**
  1. Multi-turn deep research (cricket scorecard, exact figures, URLs).
  2. Multi-tool strategy pivots (`read_file` fails $\rightarrow$ `bash chmod` succeeds).
  3. Continuous chunked reading (`start_line` / `end_line` offsets).
  4. State mutations and verification reads (Read $\rightarrow$ Edit $\rightarrow$ Verify).
  5. Cross-domain topic switching without "zombie context" contamination.

---

## 3. Work State & Milestone Breakdown

### Completed & Validated
- **Plan 01 (Model Profiling & Config):** Shipped to framework. Resolves context window and dynamic tier ratios (`masking_ratio`, `compression_ratio`) from `model_config` dict.
- **Plan 02 (Core Engine 1 Bug Fixes):**
  - *Gate Inversion Fix:* Independent gating for masking and compression.
  - *100k Floor Removal:* Compression threshold scaled dynamically to model window.
  - *In-Place Session Persistence:* Compacted context written back to `session.messages`.
  - *Hard Truncation Backstop:* Atomic tool-call block protection and pinned user corrections.
- **Trace Investigation on Empirical Runs:** Analyzed `episodic_stress_...json` revealing the post-compression truncation butcher pathology.

### Active Debates & Work in Progress
- **Refining Stage 1 & Stage 2:** Moving away from static English verb lexicons and domain regexes (`YEAR_PATTERN`, `PKG_PATTERN`, `_EDIT_WORDS`).
- **Placement of Tool Distillation:** Resolving whether distillation belongs at ingestion, before masking, or before compression.
- **Active Turn Elasticity:** Solving the 1,200-char truncation bug that blinded the agent when reading a 20k-char file.

### Blockers / Open Design Challenges
1. **Tool Starvation:** Active turn tool outputs getting truncated before the model can inspect them.
2. **Double-Jeopardy Truncation:** Compression running and saving 1,000 tokens, only for emergency truncation to immediately delete 5,000 tokens afterward.
3. **Lexicon Fragility:** Tool classification breaking when novel tools appear (`email_scan`, `email_post`, `sql_exec`).

---

## 4. Key Findings & Empirical Trace Evidence

From our deep analysis of `episodic_stress_20261026-125733.json` and real interactive runs:

1. **The Post-Compression Truncation Trap:**
   - In Payload 2 of the trace, context reached 7,537 tokens (budget was 5,000).
   - Compression summarized 6 messages, reducing context to 6,458 tokens.
   - Because 6,458 was still $> 5,000$, Emergency Truncation triggered immediately and chopped the payload down to 1,317 tokens!
   - *Finding:* The compression LLM call was 100% wasted computation because the split point was hardcoded (`preserve_recent_count = 10`) rather than budget-driven.

2. **The "Active Turn Choke" Pathology:**
   - In live testing, the user asked the agent to *"Study `test_02_episodic_context_pipeline.py`"*.
   - `read_file` returned 20,015 characters.
   - The pre-cleaner had `max_terminal_chars = 1200`. It sliced 20,015 chars down to 1,200 chars.
   - *Finding:* The model received only the first 30 lines of imports/comments. The context manager actively destroyed the agent's ability to answer the question.

3. **Compression Narrative Degradation:**
   - The compressor emitted: *"Verification: Data cross-referenced across four independent sources with no contradictions found"* despite raw sources having discrepancies.
   - It dropped actual URLs while claiming *"Source URLs are preserved"*.
   - It produced token fragments like *"Integration involves i, Mimi, Qwen-Audio..."*.
   - *Finding:* Open-ended narrative compression prompts fail. The compressor must be bound to a strict, structured schema.

---

## 5. Architectural Proposals & The Core Debate Topics

### Debate Topic A: The Placement & Behavior of Tool Distillation

There are three competing schools of thought on where to distill/truncate tool results:

#### School 1: Ingestion Distillation (At Tool Return)
- **Concept:** As soon as a tool returns, cap it immediately (e.g. 1,200 chars), spill the rest to disk, and pass only a snippet.
- **Pros:** Completely eliminates prompt token explosion on small models.
- **Cons:** **Blinds the agent on the active turn.** If the agent asked to read a file, it only gets 30 lines. Causes hallucinations and task failure.

#### School 2: Deferred Distillation (Just Before Masking / Compression)
- **Concept:** Leave tool output 100% untouched at tool return. Only distill or mask when total context pressure hits 75-80% of window.
- **Pros:** Agent has full fidelity on everything it just called.
- **Cons:** If a single tool call dumps 60,000 characters on an 8k or 12k model, the prompt crashes with an HTTP 400 Context Overflow on Turn 1 before masking/compression can run.

#### School 3 (Proposed Synthesis): Active Grace Period + Lifecycle Aging Decay
- **Concept:** 
  1. *Ingestion Safety Net:* Capped only if output exceeds physical model window headroom.
  2. *Active Turn Grace Period:* Current turn tool output gets dynamic headroom (e.g. up to 40% of window = 20k-24k chars) and remains **100% untouched**.
  3. *Aging Decay:* When Turn $N+1$ begins, Turn $N$'s tool output transitions to "history" and safely decays into a compact Micro-Digest or disk pointer.
- **Team Discussion Question:** Does the team agree with the Active Grace Period + Aging Decay model, or should certain tool classes (e.g. file reads vs. web searches) have different decay rules?

---

### Debate Topic B: Tool Semantic Classification (Lexicons vs. Structural Shapes)

#### The Problem:
`test_02_episodic_context_pipeline.py` currently relies on static verb dictionaries (`_EDIT_WORDS`, `_READ_WORDS`, `LOCATOR_ARG_KEYS`). When domain tools like `email_scan` and `email_post` were tested, the system misclassified them or lost the resource instance.

#### The 3-Level Proposal:
1. **Level 1 (Explicit Manifest):** Optional developer registration (`tool_manifest: {"email_post": {"effect": "mutation"}}`) for 100% certainty.
2. **Level 2 (Structural Parameter Shape Analysis):**
   - Automatically inspects arguments:
     - **Selectors** (IDs, paths, filters $\le 120$ chars, single-line).
     - **Payloads** (multiline strings, JSON bodies, markdown text $> 120$ chars).
   - Invariant: A tool accepting *Payloads* is a **Mutation/Write**. A tool with *Selectors only* is an **Observation/Read**.
3. **Level 3 (Conservative Opaque Fallback):**
   - Ambiguous tools degrade to `OPAQUE`.
   - Rule: OPAQUE tools are never purged and never purge other context.
- **Team Discussion Question:** Can the team identify any tool where a tool carrying payload arguments is strictly read-only, or where a mutating tool takes zero payload/content arguments?

---

### Debate Topic C: Structured Compression Schema (`SESSION STATE`)

#### The Problem:
Narrative summaries ("The user discussed X and agent searched Y") cause gradual memory loss over long sessions.

#### The Adopted Architecture:
The compressor must be prompted as a **State Compiler**, strictly emitting the following structure:

```markdown
## Objective
[Core user mission + active updates]

## Important Details
- **Active Model/Config**: [Name, tier, constraints]
- **Session/Task Context**: [Session ID, domain]
- **Key Entities**: [Companies, URLs, repos, DBs]
- **Constraints**: [Hard operational boundaries]
- **Assumptions**: [Guiding assumptions]

## Work State
### Completed
[Bullet list of verified outcomes with exact metrics]
### Active
[Active subtask in latest turns]
### Blocked
[Unresolved errors or missing tools]

## Next Move
[Numbered 1-3 concrete next actions]

## Key Findings
[Verified facts with mandatory citations:
- **[Entity/Topic]**: [Fact/Metric] (Source: URL or file path)]

## Relevant Files & Artifacts
[Touched files, scripts, logs]
```

- **Team Discussion Question:** How should this schema handle multi-task transitions (e.g., when the user explicitly says "forget topic A, let's switch to topic B")? Should older objectives be archived to disk under an Epoch Boundary?

---

## 6. Next Steps & Action Plan

1. **Team Debate & Feedback Collection:** Review Topics A, B, and C with the engineering team.
2. **Apply Plan 02E to Sandbox:** Implement the dynamic active headroom and aging decay in `test_02_episodic_context_pipeline.py`.
3. **Test Code Inspection Case:** Re-run the 20,015-char `read_file` test on `test_02_...py` to verify full fidelity on the active turn.
4. **Benchmark Verification:** Run `--stress --capture-payloads` to confirm zero post-compression emergency truncations.
