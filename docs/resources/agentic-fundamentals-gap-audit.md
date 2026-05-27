# Agentic Fundamentals Gap Audit

Date: 2026-04-23
Repository: Scratchy / Logicore

## Verdict Summary

- Determinism first: Partial
- Safety and boundaries: Partial
- Recoverability and durability: Partial
- Observability and error clarity: Partial
- Test-proven behavior: Partial
- Minimal blast radius changes discipline: Partial

No category is fully complete end-to-end yet.

## 1) Determinism First

Status: Partial

Present:
- Tool-call chunk reassembly uses sorted order: providers gateway logic.
- Iteration loops are bounded by max iterations.
- Tool dedup and bounded serialization helpers exist in main agent loop.

Gaps:
- Repeated tool loading can create ordering/context drift in specialized agents.
- Mutable default list in agent constructor can lead to cross-instance state risk.
- No explicit deterministic-order parity tests for tool registry load and session replay.

## 2) Safety and Boundaries

Status: Partial

Present:
- Max iteration and timeout limits exist in agent/tool paths.
- File-read size guards exist in filesystem tools.
- Tool risk categories are defined (safe/approval-required/dangerous).

Gaps:
- Command and git tools still rely on shell execution with broad command surface.
- Policy enforcement is mostly prompt/callback driven, not centrally enforced by immutable policy contracts.
- Path safety checks are not consistently centralized across all tool/file handlers.

## 3) Recoverability and Durability

Status: Partial

Present:
- SQLite stores and state save/load APIs exist.
- Cron service persists jobs and supports missed-job replay.
- Session storage paths and metadata/state persistence are implemented.

Gaps:
- Session manager delete operation does not execute true storage delete semantics.
- Resume/restart flow is not uniformly modeled as a single explicit contract across agent/memory/session subsystems.
- No exhaustive interruption-recovery test matrix proving continuity for all major flows.

## 4) Observability and Error Clarity

Status: Partial

Present:
- Logging is present across agent and provider gateway modules.
- Tool execution start/end/fail logging exists with durations in core agent.
- Some provider errors are normalized into user-facing ValueError messages.

Gaps:
- No strong typed error taxonomy shared across modules (auth/network/rate/policy/media/state/etc.).
- Many broad exception catches suppress detail or collapse root causes.
- Structured correlation fields (request_id, attempt_id, transition_id, state_version) are not consistently emitted.

## 5) Test-Proven Behavior

Status: Partial

Present:
- Large test inventory exists for release, provider smoke, tooling, and telemetry paths.
- Performance/security/packaging suites are present.

Gaps:
- Deterministic replay/order tests are not clearly enforced as a first-class contract.
- Resume/persistence parity tests are present in parts but not complete end-to-end for interruption scenarios.
- Many tests are in unused_test area, reducing confidence of CI enforcement.
- No strict mandatory CI test gate visible before publish workflow.

## 6) Minimal Blast Radius Changes

Status: Partial

Present:
- Modular layering exists (agents/providers/tools/memory/cron).
- Containment-like behavior appears in some fallback/retry branches.

Gaps:
- Foundational constructor/contract mismatches indicate high-impact breakpoints in core paths.
- Some behavior is spread across prompt guidance instead of hard policy boundaries, increasing accidental side effects.

## Highest-Priority Missing Topics To Implement

1. Unified error taxonomy and structured error objects across providers/tools/agent loop.
2. Deterministic state and ordering contract, with replay tests and stable tool registration order guarantees.
3. Central policy engine for tool safety (deny/allow/ask) enforced before execution, not only callback/prompt mediated.
4. Session durability contract cleanup, especially true delete semantics and explicit resume checkpoints.
5. Strict bounded execution hardening for shell/git and large payload paths with policy-level constraints.
6. End-to-end interruption and recovery tests across agent chat loop + persistence + restart.
7. CI hard gate that runs required deterministic, recovery, and safety suites before release/publish.
8. Constructor/API parity fixes for all agent subclasses to prevent runtime incompatibility.
9. Exception handling refactor: replace catch-all patterns with typed handling and mandatory diagnostic context.
10. Observability baseline: correlation IDs, retry transition logs, and state snapshots at divergence boundaries.

## Immediate Containment (Short Term)

1. Fix constructor mismatch and mutable defaults in core agent interfaces.
2. Correct session deletion behavior in session manager to call storage delete.
3. Stop duplicate tool-loading in copilot-oriented agent initialization.
4. Remove embedded secrets from repo scripts and stale imports in manual harness files.
5. Add release-blocking CI test workflow before package publish.

## Structural Corrections (Long Term)

1. Introduce shared error classes and error codes with classification mapping.
2. Introduce deterministic runtime contract document and tests for order/replay parity.
3. Introduce explicit persistence model with checkpoint versioning and resume invariants.
4. Introduce central policy middleware for tool execution safety and boundary checks.
5. Promote critical tests from experimental/unused suites into required pipelines.

## QueryEngine and Processing Core Track (Required)

Status: Partial

### A) Input Processing Contract

Present:
- Input enrichment and reference parsing exists before model execution in core agent flow.
- Some bounded extraction behavior exists (source limits and truncation behavior in enrichment path).

Missing:
- No explicit, typed policy decision payload emitted before query path (allow/deny/ask object).
- No uniform should-query/no-query branch object with deterministic reason code.
- No standard artifact capturing raw payload -> normalized payload with deterministic hash.

### B) QueryEngine Turn Orchestration Contract

Present:
- Session stores user message before model call in chat flow.
- Tool list assembly and model invocation envelope are present in runtime loop.
- Iteration and terminal exits are bounded by max iterations and terminal branches.

Missing:
- No canonical turn-start init event schema enforced across all agents.
- Tool pool policy prefiltering is not centralized as a deterministic policy stage.
- Terminal result subtype taxonomy is not consistently emitted as structured event records.

### C) Query Loop Transition Contract

Present:
- Transition-like logic exists for retry/fallback in model-call errors.
- Recovery attempts are partially bounded through loop limits/timeouts.

Missing:
- Transition reasons are not consistently encoded as explicit event enums.
- Retry branch decisions are string/error-message driven in places, not error-class driven.
- Max-output continuation path is not modeled as a first-class deterministic transition contract.

### D) Persistence and Resume Coupling Contract

Present:
- Persistence primitives exist for sessions/state and cron restart recovery.
- Resume-like behavior exists in cron missed-job recovery and memory/session loading.

Missing:
- No single transcript/event-log parity validator for checkpoint boundaries.
- No reconstructed leaf-chain contract validation for resumed state.
- Session delete/cleanup semantics are inconsistent at manager level.

## Post-Change Delta (Latest User Changes)

Scope reviewed:
- sandbox/copilot_models_pack/src/auth.py
- sandbox/copilot_models_pack/src/copilot_provider_sandbox.py
- sandbox/copilot_models_pack/scripts/run_sandbox_demo.py

Improved:
1. Better auth error signaling in sandbox via dedicated CopilotAuthError type.
2. Bounded polling timeout and non-interactive fail-fast path are now explicit.
3. Durable token reuse path via keyring has been added.
4. Unauthorized path re-auth behavior exists in sandbox provider chat calls.

Still missing (sandbox track):
1. Structured logging/correlation metadata (request_id, attempt_id, transition_id).
2. Explicit retry budget object and event timeline for auth/model calls.
3. Deterministic test suite for sandbox auth + reauth + non-interactive branches.
4. Formal error taxonomy alignment between sandbox and core provider stack.
