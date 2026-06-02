---
title: Changelog
description: Release notes and notable documentation updates.
---

# Changelog

## 2026-05-28

### Phase 1 — Provider Resilience
- Added `logicore/providers/availability.py`: `ModelAvailabilityService`, `ProviderHealth`, `AvailabilityConfig`, `HealthState`, `FailureCategory` — health tracking, exponential-backoff cooldowns, automatic failover chains.
- Added `logicore/providers/policies.py`: `RetryPolicy`, `RetryIterator`, `RetryAttempt`, `FallbackResolver`, `@with_retry` decorator, and four presets (`DEFAULT`, `AGGRESSIVE`, `CONSERVATIVE`, `NO_RETRY`).
- Updated `logicore/providers/base.py`: added `ProviderCapability` enum, `health_check()`, `get_provider_id()`, `supports()`, `get_capabilities()`, `get_metadata()` to base class.
- Updated `logicore/providers/gateway.py`: added `ResilientGateway` (wraps any provider with retry + failover; supports mid-stream retry) and `get_resilient_gateway()` factory.
- 48 new tests in `tests/test_availability.py`, all passing.

### Phase 2 — Execution Hooks & Thought Parsing
- Added `logicore/runtime/hooks/`: `HookSystem`, `HookPoint` (8 points), `HookAction` (6 actions), `HookContext`, `HookResult`, `HookRegistration` — priority-ordered hook execution, sync/async support, isolated error handling.
- Added `logicore/runtime/reasoning/thought_parser.py`: `ThoughtParser`, `ThoughtAnalysis`, `ParsedThought`, `ThoughtType` — extracts structured reasoning from model responses (`**Subject**:`, `<thinking>`, `Step N:`, chain-of-thought phrases).
- Updated `logicore/runtime/reasoning/controller.py`: `ReasoningController.register_hooks()`, `adjust_for_response()`, `analyze_response()`, `create_before_model_hook()`, `create_after_model_hook()` — automatic reasoning-level escalation.
- 28 new tests in `tests/test_hooks.py`, all passing. (76 tests total)

### Documentation
- New: `docs/concepts/providers/provider-resilience.md` — full resilience deep-dive with mermaid diagram, API tables, full working example.
- New: `docs/concepts/hooks/hooks.md` — hooks landing page.
- New: `docs/concepts/hooks/hooks-overview.md` — all hook points, actions, context fields, and four worked examples.
- New: `docs/concepts/agents/reasoning-hooks.md` — `ThoughtParser` API, complexity scoring, `ReasoningController` hook integration.
- Updated: `docs/concepts/providers/providers.md` — replaced manual failover pattern with `ModelAvailabilityService` example.
- Updated: `docs/concepts/providers/providers-overview.md` — added Provider Health & Automatic Failover section with mermaid.
- Updated: `docs/concepts/agents/agents-overview.md` — added Execution Hooks section and thought-aware reasoning section with hook-flow diagram.
- Updated: `docs/introduction.md` — added Provider Resilience and Execution Hooks capabilities; updated architecture diagram; expanded "The Problem Logicore Solves" table.

## 2026-03-16
- Fixed Ollama provider issues regarding custom tool call result handling.
- Fixed tool call error handling truncation and false failures.
- Added a built-in tool for managing cron jobs.

## 2026-03-14
- Migrated documentation to Mintlify and aligned navigation layout.
- Added provider comparison and expanded concept guides.

## 2026-03-06
- Documented memory judge system internals.
- Expanded API reference for agents, tools, and memory stores.

## 2026-03-04
- Initial public documentation for Logicore agents and quickstart.
