# Logicore Configuration Restructure — Design & Execution Plan

**Status:** Design only (no code yet), owner approval required before implementation.
**Scope:** Centralize ALL config resolution inside `logicore/config`. The user owns a single `.env`; every other module imports config and never reads `os.environ` directly.

---

## 0. Current state (what I found)

| Location | What it does | Problem |
|---|---|---|
| `logicore/config/settings.py` | Loads dotenv once (line 19), singleton `settings`, central `_get_env`/`_get_toml` helpers. Exposes `STORAGE_ROOT` (line 347, **not** `LOGICORE_` prefixed). | Does **not** own provider API keys (those live in `get_api_key` reading `os.getenv` at lines 80–96). Still carries dead TOML loading `_toml_config`/`_get_toml` (lines 21–44). |
| `logicore/storage/config.py` | `StorageConfig.from_env()` re-reads `STORAGE_ROOT` + `LOGICORE_STORAGE_*` directly (lines 49, 179–183). | **Duplicate** env reads; second source of truth. |
| `logicore/memory/manager.py` | `resolve_memory_dir` reads `LOGICORE_MEMORY_DIR` directly (line 48). | **Duplicate** env read. |
| `logicore/runtime/config.py` | `RuntimeConfig.from_settings()` re-reads ~30 `LOGICORE_*` env vars (lines 319–385). | **Duplicate** env reads; should pull from `settings` singleton, not re-`getenv`. |
| `logicore/agent/chat_orchestrator.py` | Reads `LOGICORE_TOOL_BUDGET_*` directly (lines 86–92). | **Duplicate** env read. |
| `logicore/providers/*.py` | Each reads `os.environ.get(...)` for api_key/endpoint (groq_provider:18, gemini_provider:18, openai_provider:21, azure_provider:36–37, custom_provider:50–80). | Should fall back to `config`, not raw `os.environ`. |
| `logicore/tools/media.py` | Reads `GOOGLE_API_KEY`, `GOOGLE_CX`, `YOUTUBE_API_KEY` directly (lines 51–52, 98, 145–146). | **Duplicate** env read. |
| `logicore/config/prompts.py`, `prompt_builder.py` | Prompt text + section builder only. | No env reads (fine), but prompt strings still say `.logicore/tasks` under cwd — must be updated to `~/.logicore`. |

**Task 1 (paths) current inconsistency:** `storage` and `memory` correctly use `~/.logicore` (user home). But **tasks** (`tasks/store.py:182`), **sessions** (`tasks/session_progress.py:63`), and **plans** (`runtime/planner/service.py:250–251`) build `{cwd}/.logicore/...` via `workspace_root or os.getcwd()` (`agent/base.py:144`). Task 1's goal is exactly to flip these to `~/.logicore` under config-controlled roots. This plan makes that trivial by adding `settings.paths.*`.

---

## 1. Proposed `.env` schema (single user-owned file, repo root)

All variables use the `LOGICORE_` prefix **except** provider-native keys (see note). Consistent, grouped, with sensible defaults handled in code.

### A. Provider credentials (keep provider-native names — they are the de-facto standard)
```
# LLM providers
GROQ_API_KEY=
GEMINI_API_KEY=
GOOGLE_API_KEY=            # gemini alt / used by media tools
OPENAI_API_KEY=
AZURE_OPENAI_API_KEY=      # azure_provider falls back to this
AZURE_ENDPOINT=            # https://<res>.openai.azure.com
AZURE_OPENAI_ENDPOINT=     # alt
ANTHROPIC_API_KEY=
EXA_API_KEY=
CUSTOM_PROVIDER_API_KEY=   # generic custom provider
CUSTOM_API_KEY=            # alt
CUSTOM_PROVIDER_ENDPOINT=
CUSTOM_MODEL_ENDPOINT=
```
> Why keep native names: every provider SDK (`openai`, `groq`, `google-genai`) auto-reads its own env var, and users copy these from provider dashboards. `config` wraps them via `get_api_key(provider)`.

### B. Provider / model defaults
```
LOGICORE_DEFAULT_PROVIDER=ollama
LOGICORE_DEFAULT_MODEL=gpt-oss:20b-cloud
LOGICORE_EMBEDDING_PROVIDER=ollama
LOGICORE_EMBEDDING_MODEL=qwen3-embedding:0.6b
LOGICORE_OLLAMA_URL=http://localhost:11434
```

### C. Storage root (single master knob → all state under `~/.logicore`)
```
LOGICORE_STORAGE_ROOT=~/.logicore        # base for ALL persisted state
LOGICORE_MEMORY_DIR=                      # default: $ROOT/memory
LOGICORE_TASKS_DIR=                       # default: $ROOT/tasks
LOGICORE_SESSIONS_DIR=                    # default: $ROOT/sessions
LOGICORE_PLANS_DIR=                       # default: $ROOT/plans
LOGICORE_SNAPSHOTS_DIR=                   # default: $ROOT/snapshots
LOGICORE_ASSETS_DIR=                      # default: $ROOT/assets
LOGICORE_LANCEDB_PATH=                    # default: $ROOT/lancedb_data
LOGICORE_STORAGE_DB_URL=                  # empty → sqlite at $ROOT/database/logicore.db
LOGICORE_STORAGE_DB_PASSWORD=
LOGICORE_STORAGE_SNAPSHOT_ENABLED=true
LOGICORE_STORAGE_MEDIA_ROOT=              # local path or s3://bucket/prefix
```

### D. Runtime / agentic loop
```
LOGICORE_MAX_TURNS=60
LOGICORE_MAX_HISTORY=100
LOGICORE_HTTP_TIMEOUT=30
LOGICORE_DEBUG=false
LOGICORE_LOOP_DETECTION_ENABLED=true
LOGICORE_LOOP_TOOL_THRESHOLD=5
LOGICORE_LOOP_CONTENT_THRESHOLD=10
LOGICORE_LOOP_LLM_FALLBACK=true
LOGICORE_CONTEXT_MAX_TOKENS=128000
LOGICORE_COMPRESSION_RATIO=0.85
LOGICORE_TOOL_TIMEOUT=60
LOGICORE_TOOL_COOLDOWN=60
LOGICORE_TOOL_DEDUP=true
LOGICORE_RETRY_MAX_ATTEMPTS=3
LOGICORE_RETRY_BASE_DELAY=500
LOGICORE_TELEMETRY_ENABLED=true
LOGICORE_PROMPT_CACHE_ENABLED=true
LOGICORE_PROMPT_CACHE_TTL=300
LOGICORE_PROMPT_CACHE_MAX_ENTRIES=100
LOGICORE_TOOL_BUDGET_MODE=off             # off|on|auto
LOGICORE_TOOL_BUDGET_MAX_TOKENS=
LOGICORE_TOOL_BUDGET_PCT=25
```

### E. Server / deployment
```
LOGICORE_MODE=local                       # local|cloud
LOGICORE_ENVIRONMENT=development
LOGICORE_HOST=127.0.0.1
LOGICORE_PORT=8000
LOGICORE_FRONTEND_PORT=3000
LOGICORE_CORS_ORIGINS=                     # comma-separated
```

### F. Cloud services
```
LOGICORE_SUPABASE_URL=
LOGICORE_SUPABASE_KEY=
LOGICORE_BLOB_READ_WRITE_TOKEN=
LOGICORE_ACR_REGISTRY=
LOGICORE_AKS_CLUSTER=
LOGICORE_AKS_RESOURCE_GROUP=
```

### G. SMTP (email tool)
```
LOGICORE_SMTP_HOST=smtp.gmail.com
LOGICORE_SMTP_PORT=587
LOGICORE_SMTP_USER=
LOGICORE_SMTP_PASSWORD=
LOGICORE_SMTP_FROM_EMAIL=
LOGICORE_SMTP_USE_TLS=true
```

### H. Search/media extras
```
LOGICORE_GOOGLE_CX=                        # cse id for media tools
LOGICORE_YOUTUBE_API_KEY=
```

---

## 2. Config module ownership model

**Invariant:** `logicore/config` is the ONLY package that imports `os`/`dotenv` for configuration. Everything else imports typed objects from `config`.

### New file: `logicore/config/env.py` (the single env gateway)
- Calls `load_dotenv()` once at import (idempotent — `python-dotenv` caches).
- Defines **one canonical constants table** `ENV_NAMES` (single source of every variable name) so names live in exactly one place.
- Exposes `_raw(key, default=None)` → `os.getenv` (the only `os.environ` read in the repo).
- Exposes `_expand(path)` → `Path(os.path.expanduser(path))`.
- Exposes `resolve_storage_path(subdir)` → `(_expand(LOGICORE_STORAGE_ROOT) / subdir)`.
- This is the only module allowed to touch `os.environ`.

### `logicore/config/settings.py` (the typed facade)
- Keep the `LogicoreSettings` dataclass (alias `AgentrySettings` kept for back-compat, then deprecated).
- **Remove** `_toml_config`/`_get_toml`/TOML loading entirely (dead code — see §5).
- Replace `_get_env`/`_get_bool`/`_get_int` with thin wrappers over `env._raw`.
- Add a `paths` sub-object (dataclass `PathSettings`) computed from `LOGICORE_STORAGE_ROOT` + per-dir overrides:
  - `storage_root`, `memory_dir`, `tasks_dir`, `sessions_dir`, `plans_dir`, `snapshots_dir`, `assets_dir`, `lancedb_path`, `database_url`.
  - Each path property: explicit override env (`LOGICORE_MEMORY_DIR` etc.) > `<ROOT>/<subdir>` > error-free default.
- Add `api_keys` accessor: `get_api_key(provider)` delegates to `env._raw` on the provider-native names (same mapping currently in settings.py:80–96). Centralized here, not in providers.
- Keep `create_storage()` but rebuild it from `settings.paths` (no separate `LOGICORE_STORAGE_*` reads).

### Precedence rule (explicit > ENV > default)
Provide a module-level helper used everywhere:
```
def resolve(explicit, env_name, default):
    if explicit is not None: return explicit
    v = env._raw(env_name)
    return v if v is not None else default
```
This is the mechanism that satisfies "explicit constructor param overrides env." Applied at every call site (agent api_key, memory_dir, tasks_dir, etc.).

### `logicore/config/__init__.py` (exports)
```
from .env import load_dotenv, get_raw, resolve_storage_path
from .settings import settings, LogicoreSettings, get_api_key
from .settings import PathSettings  # settings.paths
__all__ = [...]
```
Importing `logicore.config` triggers `load_dotenv()` exactly once.

---

## 3. Refactor boundary (modules that must stop reading `os.environ`)

From the grep inventory, these read env directly and must import from `config`:

| Module | Current read | New source |
|---|---|---|
| `logicore/storage/config.py` (49, 179–183) | `STORAGE_ROOT`, `LOGICORE_STORAGE_*` | `settings.paths.*` |
| `logicore/memory/manager.py` (48) | `LOGICORE_MEMORY_DIR` | `settings.paths.memory_dir` |
| `logicore/runtime/config.py` (319–385) | ~30 `LOGICORE_*` | read from `settings` singleton (no `getenv`) |
| `logicore/agent/chat_orchestrator.py` (86–92) | `LOGICORE_TOOL_BUDGET_*` | `settings` (add `tool_budget_*` fields) |
| `logicore/providers/groq_provider.py` (18) | `GROQ_API_KEY` | `api_key or get_api_key("groq")` |
| `logicore/providers/gemini_provider.py` (18) | `GEMINI_API_KEY`/`GOOGLE_API_KEY` | `api_key or get_api_key("gemini")` |
| `logicore/providers/openai_provider.py` (21) | `OPENAI_API_KEY` | `api_key or get_api_key("openai")` |
| `logicore/providers/azure_provider.py` (36–37) | `AZURE_*` | `api_key/endpoint or get_api_key("azure")` |
| `logicore/providers/custom_provider.py` (50–80) | `CUSTOM_*` | `api_key/endpoint or get_api_key("custom")` |
| `logicore/tools/media.py` (51–52, 98, 145–146) | `GOOGLE_API_KEY`, `GOOGLE_CX`, `YOUTUBE_API_KEY` | `settings`-backed accessor |
| `logicore/config/settings.py` (80–96) | `os.getenv` in `get_api_key` | `env._raw` (already in config, just consolidate) |

`os.getenv`/`os.environ` in `logicore/mcp/client.py:90` (env passthrough to subprocess) and `logicore/context_engine` (pathlib) are NOT config reads — leave them. `os.getcwd()` in prompts.py/session_progress is runtime cwd (legit, but the `.logicore/tasks` *text* in prompts.py must be corrected to `~/.logicore`).

---

## 4. Backward compatibility / migration

- **`LOGICORE_MEMORY_DIR`** (existing users): still honored, but now resolved inside `settings.paths.memory_dir` (override over `STORAGE_ROOT/memory`). No behavior change.
- **`LOGICORE_STORAGE_*` / `STORAGE_ROOT`** (existing users): `STORAGE_ROOT` renamed to `LOGICORE_STORAGE_ROOT` for prefix consistency. Keep a 1-release shim: `env._raw("LOGICORE_STORAGE_ROOT") or env._raw("STORAGE_ROOT")`. Log a `DeprecationWarning` when old name used. Remove shim next major.
- **`logicore.toml` users**: TOML support is being retired (see §5). Provide a one-time migration note + a small `scripts/migrate_toml_to_env.py` that reads existing `logicore.toml` and emits the equivalent `.env` keys. Ship in this PR; delete the script after one release. TOML keys map 1:1 (e.g. `[storage] root` → `LOGICORE_STORAGE_ROOT`).
- **Agent `api_key=` constructor param**: unchanged — remains supported. Providers keep `api_key or get_api_key(name)`. Env/config is the *default*; explicit wins. No migration needed.
- **`AgentrySettings` alias & module-level `MODE`/`DEBUG`/`OLLAMA_URL` exports**: keep as deprecated re-exports in `settings.py` for one release, emit `DeprecationWarning`, point to `settings.*`.

---

## 5. Dead-code cleanup

1. **TOML loader** in `config/settings.py` (`_toml_config`, `_get_toml`, `tomllib`/`toml` try-blocks, lines 21–44) — remove. All precedence becomes ENV > default. (Superseded by §4 migration script.)
2. **Duplicate `os.getenv` reads** in `storage/config.py` and `runtime/config.py` — delete; consume `settings` instead.
3. **`STORAGE_ROOT` non-prefixed helper in `storage/config.py`** (`_default_base_dir`, lines 42–52) — replace with `settings.paths.storage_root`.
4. **Per-provider `os.getenv` in `config/settings.py:get_api_key`** stays but moves its lookups through `env._raw` (no logic dup).
5. **`LogicoreSettings = AgentrySettings` alias** — deprecate, then remove next release.

---

## 6. Concrete file-level plan & execution order

**Phase 1 — Foundations (no behavior change yet)**
1. `logicore/config/env.py` — new. `load_dotenv()` once, `ENV_NAMES` constants, `_raw`, `_expand`, `resolve_storage_path`. **Only** file touching `os.environ`.
2. `logicore/config/settings.py` — remove TOML block; rewrite `_get_*` to wrap `env._raw`; add `PathSettings` (`paths`) computed from `LOGICORE_STORAGE_ROOT` + per-dir overrides; move `get_api_key` to use `env._raw`; keep `create_storage()` but build from `settings.paths`; keep deprecated aliases.
3. `logicore/config/__init__.py` — export `settings`, `get_api_key`, `load_dotenv`, `PathSettings`; importing triggers dotenv.

**Phase 2 — Kill duplicate readers**
4. `logicore/storage/config.py` — `DatabaseConfig/SnapshotConfig/MediaConfig` + `from_env()` now pull from `settings.paths`; drop `_default_base_dir`/`LOGICORE_STORAGE_*` reads.
5. `logicore/memory/manager.py` — `resolve_memory_dir` uses `settings.paths.memory_dir` (keep `custom_path` param precedence).
6. `logicore/runtime/config.py` — `from_settings()` reads values from `settings` singleton (no `getenv`); add `LOGICORE_TOOL_BUDGET_*` mapping into `settings` or a small `ToolBudgetConfig` built from `settings`.
7. `logicore/agent/chat_orchestrator.py` — replace `os.environ.get` budget reads with `settings` fields.
8. `logicore/providers/{groq,gemini,openai,azure,custom}_provider.py` — `api_key or get_api_key("<name>")`, `endpoint or settings...` ; drop direct `os.environ`.
9. `logicore/tools/media.py` — use `settings`-backed accessors for `GOOGLE_API_KEY/CX`, `YOUTUBE_API_KEY`.

**Phase 3 — Task 1 path migration (state → `~/.logicore`)**
10. `logicore/tasks/store.py:182` — `self.tasks_dir = settings.paths.tasks_dir / task_list_id` (remove `base_dir/".logicore"/"tasks"`).
11. `logicore/tasks/session_progress.py:63` — `self._session_dir = settings.paths.sessions_dir / session_id`.
12. `logicore/runtime/planner/service.py:250–251` — `self.plans_dir = settings.paths.plans_dir` (drop `project_dir/".logicore"/"plans"`).
13. `logicore/agent/base.py:144,158,775,784` — stop defaulting `_task_base_dir` to `os.getcwd()`; pass `settings.paths.tasks_dir`/`sessions_dir` into `TaskStore`/`SessionProgressWriter`; `PlanService(project_dir=None)` now resolves to config root.
14. `logicore/config/prompts.py` — fix copy: `.logicore/tasks` → `~/.logicore/tasks`, etc.

**Phase 4 — User-facing + cleanup**
15. `.env.example` — rewrite to the full §1 schema (replace the 26-line current file).
16. `.env` — add new keys (keep existing `EXA_API_KEY`, `GEMINI_API_KEY`), migrate `STORAGE_ROOT`→`LOGICORE_STORAGE_ROOT` if present.
17. `scripts/migrate_toml_to_env.py` — one-time TOML→`.env` helper (§4).
18. Deprecation shims for `STORAGE_ROOT` and `AgentrySettings` (warn, then remove next major).
19. Docs/README note: "all config via `.env`; never read `os.environ` in app code."

**Verification before merge**
- `grep -rn "os.environ\|os.getenv" logicore --include=*.py` → only `config/env.py` and the legit non-config sites (`mcp/client.py`, `context_engine`) remain.
- Run a smoke test: `Agent(provider="groq")` with `GROQ_API_KEY` only in `.env`; `Agent(provider="groq", api_key="sk-test")` overrides; confirm tasks/sessions/plans land under `~/.logicore`.

---

## 7. Integration with the approved Task 1 plan

Task 1 ("move task/session/plan history to `~/.logicore` under config-controlled roots") is **fully delivered by Phase 3 of this plan**. The reason those three path sites currently use cwd is that they have no centralized root — this config restructure introduces `settings.paths.{tasks_dir,sessions_dir,plans_dir}` derived from `LOGICORE_STORAGE_ROOT`. Once config owns the roots, Task 1 becomes a 4-line change per file (steps 10–13) with zero scattered env reads. Memory/snapshots/assets already use `~/.logicore` and simply get folded into the same `settings.paths` facade for consistency. This PR should land **before or alongside** Task 1 so Task 1 consumes `settings.paths` rather than re-implementing its own `~/.logicore` logic.

---

## Open questions for owner
1. Rename `STORAGE_ROOT` → `LOGICORE_STORAGE_ROOT` now (with shim) or keep `STORAGE_ROOT` as the one exception? (Recommend rename + 1-release shim.)
2. Retire `logicore.toml` entirely (recommend yes) or keep read-only fallback for one more release?
3. Keep provider-native key names (`GROQ_API_KEY`) or also accept `LOGICORE_GROQ_API_KEY` aliases? (Recommend native-only to avoid two sources of truth.)
