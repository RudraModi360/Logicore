# Product Owner Progress Tracker: Harness Modernization & Context Architecture

## Project Overview
This repository contains the engineering handoff specifications for modernizing Logicore's context management, model adaptability, and prompt architecture. 

As Product Owner, use this document to track implementation status, dependencies, and test verification across your developer and their agent teams.

---

## Feature Roadmap & Status Cockpit

| ID | Feature Name | Target Scope | Status | Dependent On | Plan Link |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **00** | **Phase 0: Sandbox Prototype & Replay Bench** | `experiments/` (Safe pre-prod) | `SHIPPED (PO signed off)` | None | [Plan 00](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/00_PHASE0_SANDBOX_PROTOTYPE_AND_BENCHMARK.md) |
| **01** | **Part A: Model Config & Context Profiling** | `model_config` dict & fallback notice | `SHIPPED (commit 03a1dee)` | Plan 00 | [Plan 01](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/01_FEATURE_MODEL_CONFIG_AND_PROFILING.md) |
| **02** | **Part B: Context Engine 1 Fixes (Masking & Compaction)** | Gate inversion, 100k floor, persistence, hard truncation | `SHIPPED (framework port, PO signed off; uncommitted)` | Plan 01 | [Plan 02](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/02_FEATURE_ENGINE1_SESSION_CONTEXT_AND_MASKING.md) · [Plan 02B](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/02B_FRAMEWORK_HARDENING_AND_HARD_TRUNCATION.md) |
| **02C** | **Part B.2: Episode-Based Lifecycle Deduplication** | Cross-tool pivot collapse & scaffolding reduction | SANDBOX TESTING | Plan 02 | [Plan 02C](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/02C_EPISODE_BASED_DEDUPLICATION_AND_PRECLEANING.md) |
| **02D** | **Part B.3: Generalized Tool Semantic Classifier** | Replaces static lexicons with parameter shape analysis | PLAN DRAFTED | Plan 02C | [Plan 02D](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/02D_GENERALIZED_SEMANTIC_CLASSIFIER_AND_STRUCTURAL_TRACKER.md) |
| **03** | **Part C: Context Engines 2 & 3 Fixes (Caching & Dedup)** | FileStateCache & serialization reused flag | `READY FOR DEV` | Plan 02 | [Plan 03](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/03_FEATURE_ENGINE2_AND_3_CACHING_AND_DISTILLATION.md) |
| **04** | **Part D: Action Transparency & Walkthrough Decoupling** | Ephemeral scratchpad (zero memory bloat) | `READY FOR DEV` | Plan 02 | [Plan 04](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/04_FEATURE_ACTION_TRANSPARENCY_WALKTHROUGH.md) |
| **05** | **Part E: Microlithic Prompts & Tool Bundles** | 44k -> 2.5k prompt, core tools default | `READY FOR DEV` | Plan 01, 04 | [Plan 05](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans/05_FEATURE_MICROLITHIC_PROMPTS_AND_TOOL_BUNDLES.md) |

---

## Dependency Graph

```mermaid
flowchart TD
    P0["Plan 00: Phase 0 Sandbox & Benchmark<br/>(experiments/replay_context_bench.py)"] --> P1["Plan 01: Model Config & Profiling<br/>(runtime/model_profile.py)"]
    P1 --> P2["Plan 02: Engine 1 Session Context<br/>(Gate Inversion & 100k Floor Fix)"]
    P2 --> P3["Plan 03: Engines 2 & 3 Caching<br/>(FileStateCache & Reused Flag)"]
    P2 --> P4["Plan 04: Action Transparency<br/>(Ephemeral Walkthrough Decoupling)"]
    P1 & P4 --> P5["Plan 05: Microlithic Prompts<br/>(Core Bundles & Schema Dedup)"]
```

---

## Handoff Instructions for the Developer & Agent Team

Each feature plan in [`docs/feature_plans/`](file:///c:/Users/rudra/Desktop/Scratchy/docs/feature_plans) is self-contained. The developer or agent team should execute them sequentially:

1. **Start with Plan 00 (Sandbox Validation):**
   - Implement `experiments/replay_context_bench.py` and run it on existing traces in `debug/traces/`.
   - Implement `experiments/prototype_agent.py` and test interactive multi-turn chatting.
   - Present the simulation results to the Product Owner for sign-off.
2. **Execute Production Changes (Plans 01 through 05):**
   - For each part, follow the exact diff specifications provided in the plan file.
   - Run the specified unit tests (`pytest tests/test_...`) to ensure 100% pass rate.
   - Mark the status in this tracker as `IN PROGRESS` then `TESTED` then `SHIPPED`.

---

## Quality & Acceptance Gates

| Feature | Primary Verification Check | Success Metric |
| :--- | :--- | :--- |
| **Plan 00** | Run `python experiments/replay_context_bench.py` | Shows >60% token reduction via masking & compression on trace replay |
| **Plan 01** | Run `pytest tests/test_model_profile.py` | Correctly parses 32k small tier & prints friendly notice for unknown models |
| **Plan 02** | Run `pytest tests/test_context_manager.py` | Masking triggers at 60% window; summarization triggers on 32k window without `SKIPPED_SMALL` |
| **Plan 03** | Run `pytest tests/test_file_cache.py` | Unchanged file reads return cache confirmation stubs; `reused: true` logged |
| **Plan 04** | Run `pytest tests/test_walkthrough.py` | `session.messages` never contains `"Task execution is complete"` or synthetic user turns |
| **Plan 05** | Run `pytest tests/test_prompt_builder.py` | Default prompt drops from 44,402 chars to < 2,500 chars; only 10 core tools exposed |
