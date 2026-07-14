"""Per-turn tool-schema budget ("progressive tool disclosure") for Logicore.

Mirrors the design intent of the hermes-agent parent's ``tools/tool_search.py``:
a model-facing tools array is assembled every turn, and when the schema payload
would crowd out the model's context we defer the *optional / deferrable* tools
behind a small set of bridge tools.

Hard invariant (same as the parent):

* **Core tools are NEVER deferred.** Tools in ``CORE_TOOL_NAMES`` must always be
  present in the model-facing array. Always-load means always-load — no
  exceptions. Deferring a core tool would break the "narrow waist" contract the
  system prompt is written against.
* Only *deferrable* tools (MCP/plugin-provided tools, identified by a toolset
  prefix or by absence from ``CORE_TOOL_NAMES``) are eligible for deferral.
* The budget gate runs every assembly. Below threshold it is a passthrough.

This module is intentionally dependency-light so it can be imported from the
tool executor, the chat orchestrator, and tests without dragging the whole
agent graph in.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("logicore.tools.tool_budget")

# Cheap cross-provider rule of thumb: ~4 chars per token for English+JSON.
# Underestimating errs toward NOT activating tool search (safer default).
CHARS_PER_TOKEN = 4.0


# ---------------------------------------------------------------------------
# Core vs deferrable classification
# ---------------------------------------------------------------------------

# Built-in tools that are part of the agent's "narrow waist" — always loaded.
# This is the Logicore analogue of hermes-agent's ``_HERMES_CORE_TOOLS``.
CORE_TOOL_NAMES: frozenset[str] = frozenset({
    # Task management (bookkeeping, always safe + needed)
    "task_create", "task_get", "task_update", "task_list", "task_next",
    # Filesystem (core reads/writes)
    "read_file", "create_file", "edit_file", "delete_file",
    "list_files", "search_files", "fast_grep",
    # Execution
    "execute_command", "code_execute",
    # Process management
    "list_processes", "kill_process", "get_process_info",
    "get_process_output", "tail_process_output", "watch_process",
    # Web
    "web_search", "url_fetch", "image_search",
    # Git
    "git_command",
    # SmartAgent internals
    "bash", "datetime", "notes", "think",
    # Document
    "read_document", "convert_document",
    # Media
    "media_search",
    # Cron
    "add_cron_job", "list_cron_jobs", "remove_cron_job", "get_crons",
    # Plan
    "enter_plan_mode", "submit_plan", "exit_plan_mode",
    "update_plan_progress", "view_plan",
})


def is_deferrable_tool_name(name: str) -> bool:
    """Return True if a tool may be deferred behind the bridge.

    A tool is deferrable iff it is NOT a core tool AND it is not already a
    bridge tool. MCP/plugin tools that happen to shadow a core name are still
    protected (core wins) — mirroring the parent's shadowing protection.
    """
    if not name:
        return False
    # Names beginning with a toolset namespace prefix (e.g. ``mcp:``) are
    # always deferrable — they are external and not part of the narrow waist.
    if ":" in name:
        return True
    return name not in CORE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolBudgetConfig:
    """Resolved budget config for a single assembly.

    ``mode``:
      * "off"        — never defer; passthrough.
      * "on"         — always defer deferrable tools when any exist.
      * "auto"       — defer only when budget is exceeded (default).

    ``max_schema_tokens``: hard ceiling on total tool-schema tokens sent to the
    model. When the visible+deferrable schema cost exceeds this, deferrable
    tools are stripped until (core cost + bridge) is under the ceiling. In
    "auto" mode a 0/None value falls back to ``threshold_pct`` of context.
    """

    mode: str = "auto"
    max_schema_tokens: Optional[int] = None
    threshold_pct: float = 25.0

    @classmethod
    def from_raw(cls, raw: Any) -> "ToolBudgetConfig":
        if raw is None:
            return cls()
        if isinstance(raw, bool):
            return cls(mode="on" if raw else "off")
        if not isinstance(raw, dict):
            return cls()
        mode = str(raw.get("mode", "auto")).strip().lower()
        if mode not in ("off", "on", "auto"):
            mode = "auto"
        max_schema_tokens = raw.get("max_schema_tokens")
        try:
            max_schema_tokens = int(max_schema_tokens) if max_schema_tokens else None
        except (TypeError, ValueError):
            max_schema_tokens = None
        threshold_pct = max(0.0, min(100.0, float(raw.get("threshold_pct", 25.0))))
        return cls(mode=mode, max_schema_tokens=max_schema_tokens, threshold_pct=threshold_pct)


# ---------------------------------------------------------------------------
# Token estimation + classification
# ---------------------------------------------------------------------------


def estimate_tokens_from_schemas(tool_defs: Iterable[Dict[str, Any]]) -> int:
    """Estimate token cost of a tool-defs list via the chars/4 rule.

    Order-of-magnitude precision is fine — this gates an activate/skip decision
    where the cliff is typically tens of thousands of tokens.
    """
    total_chars = 0
    for td in tool_defs:
        try:
            total_chars += len(json.dumps(td, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            total_chars += len(str(td))
    return int(math.ceil(total_chars / CHARS_PER_TOKEN))


def classify_tools(tool_defs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a tool-defs list into (visible, deferrable).

    ``visible`` keeps every core tool plus anything we can't classify.
    ``deferrable`` is the candidate set for deferral.
    """
    visible: List[Dict[str, Any]] = []
    deferrable: List[Dict[str, Any]] = []
    for td in tool_defs:
        name = (td.get("function") or {}).get("name", "")
        if is_deferrable_tool_name(name):
            deferrable.append(td)
        else:
            visible.append(td)
    return visible, deferrable


# ---------------------------------------------------------------------------
# Public assembly entry point
# ---------------------------------------------------------------------------


@dataclass
class BudgetAssemblyResult:
    tool_defs: List[Dict[str, Any]]
    activated: bool
    deferred_count: int = 0
    deferred_tokens: int = 0
    budget_tokens: int = 0


def assemble_tool_defs(
    tool_defs: List[Dict[str, Any]],
    *,
    context_length: Optional[int] = None,
    config: Optional[ToolBudgetConfig] = None,
) -> BudgetAssemblyResult:
    """Return the model-facing tool-defs list after budget enforcement.

    Core tools are always retained. Deferrable tools are dropped (returned as
    deferred) when the schema budget would be exceeded. Dropped tools must be
    re-surfaced by the caller via a tool-search / discovery mechanism; this
    module only governs the *budget*, it does not inject bridge tools (that is
    the orchestrator's job, keeping the registry free of transport concerns).
    """
    if config is None:
        config = ToolBudgetConfig()

    if config.mode == "off":
        return BudgetAssemblyResult(tool_defs=list(tool_defs), activated=False)

    visible, deferrable = classify_tools(tool_defs)
    if not deferrable:
        return BudgetAssemblyResult(tool_defs=list(tool_defs), activated=False)

    total_tokens = estimate_tokens_from_schemas(tool_defs)
    core_tokens = estimate_tokens_from_schemas(visible)
    deferrable_tokens = estimate_tokens_from_schemas(deferrable)

    # Resolve the budget ceiling.
    if config.max_schema_tokens and config.max_schema_tokens > 0:
        ceiling = config.max_schema_tokens
    elif context_length and context_length > 0:
        ceiling = int(context_length * (config.threshold_pct / 100.0))
    else:
        # No context known: only defer if the schema is genuinely large.
        ceiling = 20_000

    if config.mode == "on":
        activate = True
    else:
        # auto: activate only when over budget.
        activate = total_tokens > ceiling

    if not activate:
        return BudgetAssemblyResult(
            tool_defs=list(tool_defs),
            activated=False,
            deferred_count=len(deferrable),
            deferred_tokens=deferrable_tokens,
            budget_tokens=ceiling,
        )

    # Defer everything deferrable; keep the core "narrow waist".
    logger.info(
        "tool_budget: deferring %d tools (~%d tokens) to stay under %d-token ceiling "
        "(core %d tools kept, ~%d tokens)",
        len(deferrable), deferrable_tokens, ceiling, len(visible), core_tokens,
    )
    return BudgetAssemblyResult(
        tool_defs=list(visible),
        activated=True,
        deferred_count=len(deferrable),
        deferred_tokens=deferrable_tokens,
        budget_tokens=ceiling,
    )


def deferrable_names(tool_defs: List[Dict[str, Any]]) -> frozenset[str]:
    """Return the deferrable tool names present in ``tool_defs``.

    Used as a scoping gate so a budget-restricted session can only reach tools
    it was actually granted, never arbitrary tools via a discovery bridge.
    """
    names: set[str] = set()
    for td in tool_defs:
        name = (td.get("function") or {}).get("name", "")
        if name and is_deferrable_tool_name(name):
            names.add(name)
    return frozenset(names)


__all__ = [
    "CORE_TOOL_NAMES",
    "ToolBudgetConfig",
    "BudgetAssemblyResult",
    "is_deferrable_tool_name",
    "classify_tools",
    "estimate_tokens_from_schemas",
    "assemble_tool_defs",
    "deferrable_names",
]
