# Logicore Repository Research Notes

Date: 2026-04-23
Scope: Architecture walkthrough + reliability risks

## 1. What This Repo Is Doing

Logicore is a Python framework to build tool-using LLM agents with a unified interface across local and cloud providers.

Core goals:
- Provider abstraction: one Agent API for Ollama, OpenAI, Groq, Gemini, Azure.
- Tool calling: built-in tools for files, web, git, docs, office, cron.
- Session and memory support: session persistence and optional long-term memory.
- MCP integration: dynamic external tools from MCP servers.
- Docs + publishing: Mintlify docs and PyPI publishing workflows.

## 2. Folder-Wise Notes

### Root
- `README.md`: product overview and quickstart.
- `pyproject.toml`: Python package metadata for `logicore`.
- `package.json` + `mint.json`: docs site tooling and navigation.
- `mcp.json`: default MCP server config example (Excel via `uvx`).
- `deploy/`: release scripts, changelog, and checklists.
- `test_simple.py`: manual interactive test harness.

### `logicore/`
Main library package.

- `agents/`: agent types and orchestration loops.
- `providers/`: provider wrappers + gateway normalization.
- `tools/`: built-in tool schemas and executors.
- `memory/`: project memory and session/persistence helpers.
- `simplemem/`: vector-memory integration.
- `cron/`: job scheduling and persistence.
- `document_handlers/`: file extraction/parsing for many formats.
- `skills/`: skill model + loader from SKILL.md and scripts.
- `services/`: helper services (marker/vision glue).

### `docs/`
Concept and usage docs (agents, tools, skills, memory, providers, MCP), plus install/quickstart/resources.

### `tests/`
Test suites grouped by use case:
- `release_validation/`: broad quality/perf/security/packaging checks.
- `real_providers/`: provider integration tests.
- `prefix_prompt_caching/`, `test_agent_fixes/`, `unused_test/`: targeted and experimental suites.

### `sandbox/`
Experimental Copilot model pack and auth/provider sandbox.

## 3. File-Wise Notes (Important Files)

### `logicore/agents/agent.py`
What it does:
- Main runtime loop (`chat`) with tool-call iteration.
- Builds provider via factory, gateway mediation, tool execution, callbacks, telemetry.
- Supports skills, MCP servers, and optional memory/context compression.

How it does it:
- Collects tools from internal registry + MCP managers.
- Sends normalized messages through gateway `chat` / `chat_stream`.
- Parses `tool_calls`, executes tools, appends tool results as conversation turns.
- Repeats until no tool calls or max iterations reached.

### `logicore/providers/gateway.py`
What it does:
- Unified translation layer for provider-specific payload/response formats.

How it does it:
- Defines `ProviderGateway` abstract base and concrete gateways (OpenAI-compatible, Gemini, Ollama, Azure).
- Normalizes to a common shape: role/content/tool_calls.
- Handles streaming and tool-call reconstruction across providers.

### `logicore/tools/registry.py` and `logicore/tools/__init__.py`
What it does:
- Registers built-in tools and exposes schema list and risk categories.

How it does it:
- Global registry instance loads tools on import.
- Exposes `ALL_TOOL_SCHEMAS` for agent injection.
- Risk lists (`SAFE_TOOLS`, `APPROVAL_REQUIRED_TOOLS`, `DANGEROUS_TOOLS`) guide approval flow.

### `logicore/tools/execution.py` and `logicore/tools/git.py`
What they do:
- Execute shell/python and git commands.

How they do it:
- `subprocess.run` wrappers returning structured `ToolResult`.
- Basic timeout/output handling.

### `logicore/mcp_client.py`
What it does:
- Connects to MCP stdio servers from config and proxies tool listing/execution.

How it does it:
- Async server tasks with ready/stop events.
- Maps each external tool to its source server.

### `logicore/cron/service.py` and `logicore/cron/types.py`
What they do:
- In-process cron scheduling with persistence and missed-job recovery.

How they do it:
- Background thread runs async loop.
- Supports `at`, `every`, and 5-field `cron` expressions.
- Stores execution metadata and recent run history per job.

### `logicore/skills/loader.py`
What it does:
- Loads skills from SKILL.md (+ optional Python scripts).

How it does it:
- Parses frontmatter and instructions.
- Dynamically imports script functions into callable skill tools.

### `logicore/memory/project_memory.py` and `logicore/memory/storage.py`
What they do:
- SQLite-backed project memory and persistent agent/session memory.

How they do it:
- Table-driven storage + simple retrieval patterns.
- Project memory includes FTS for keyword search.

## 4. Key Problems That Can Make It Work Poorly

Priority legend: Critical, High, Medium

### Critical
1. Agent subclass constructor mismatch can raise runtime `TypeError`.
- `SmartAgent` and `CopilotAgent` pass `capabilities=...` into `Agent.__init__`, but `Agent.__init__` does not accept that argument.
- Impact: constructing these classes can fail immediately.

2. Session delete API in manager does not actually delete sessions.
- `SessionManager.delete_session` only writes empty messages and returns success.
- Real delete exists in storage class but is not called by manager.
- Impact: stale sessions remain in DB; user-visible cleanup inconsistency.

### High
3. Mutable default argument in core agent.
- `Agent.__init__(tools: list = [])` uses a mutable default.
- Impact: cross-instance shared-state bugs if mutation occurs in edge paths.

4. Duplicate tool loading path in Copilot agent.
- `CopilotAgent` defaults `tools=True` (loads defaults in base), then calls `load_default_tools()` again.
- Impact: duplicate tool schemas, bloated prompt/tool context, unstable behavior.

5. Test harness imports provider not present in package.
- `test_simple.py` imports `logicore.providers.copilot_provider`, but provider module is not in `logicore/providers/`.
- Impact: manual validation script fails or is stale.

6. Hardcoded API key in repository script.
- `test_simple.py` contains a literal Gemini key string.
- Impact: secret leakage risk and accidental misuse.

### Medium
7. CI focuses on publish/docs but lacks mandatory test workflow.
- Existing workflows publish package/docs; no standard CI test gate before release.
- Impact: regressions can ship undetected.

8. Broad exception swallowing in multiple critical modules.
- Common `except Exception: ... pass/return` patterns in gateways/loaders/memory paths.
- Impact: silent failure modes, harder debugging, hidden bad states.

9. Skill script import is unrestricted dynamic execution.
- `SkillLoader` imports arbitrary Python in skill script folders.
- Impact: trusted-code assumption; unsafe if untrusted skill content is loaded.

10. Release/security tests include mostly structural assertions, not strict adversarial checks.
- Several tests assert object existence or no-crash behavior rather than strong policy/security guarantees.
- Impact: false confidence in robustness.

## 5. Why It Works Despite Issues

Strengths observed:
- Clear provider gateway pattern with normalized outputs.
- Good modular split between agents/providers/tools/memory/cron.
- Strong documentation coverage for conceptual onboarding.
- Broad test inventory across real providers and release scenarios.

## 6. Suggested Next Actions (Practical)

1. Fix constructor compatibility first (Agent/SmarAgent/CopilotAgent argument contract).
2. Correct session delete semantics in `SessionManager` to call storage delete.
3. Replace mutable default in `Agent.__init__` with `None` pattern.
4. De-duplicate tool loading in `CopilotAgent`.
5. Remove hardcoded key and stale provider import from `test_simple.py`.
6. Add required CI workflow: lint + unit/integration smoke before publish.
7. Tighten exception handling with structured logging and explicit failure paths.
