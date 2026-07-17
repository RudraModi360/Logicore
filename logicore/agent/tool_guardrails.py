"""Tool-Call Guardrails: Anti-Repetition System for Logicore.

Mirrors the design of the hermes-agent parent's ``agent/tool_guardrails.py``.
This module tracks three distinct loop patterns per turn and returns decisions;
runtime code owns enforcement.

The controller is **side-effect-free** — it only decides, never executes.
The conversation loop owns enforcement.

Three tracking patterns:
1. **Exact call repetition**: Same tool + same args (SHA-256 hash) failing repeatedly
2. **Same-tool failures**: Same tool with ANY args failing repeatedly
3. **No-progress (idempotent)**: Read-only tool returning identical results repeatedly

When a hard_stop fires, it injects a synthetic tool result into the conversation
as a course-correction signal the LLM can reason about.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

logger = logging.getLogger("logicore.agent.tool_guardrails")


# ---------------------------------------------------------------------------
# Tool Classification — Idempotent vs Mutating
# ---------------------------------------------------------------------------

# Read-only tools: tracked for no-progress detection
IDEMPOTENT_TOOLS: FrozenSet[str] = frozenset({
    "read_file", "list_files", "search_files", "fast_grep",
    "glob", "web_search", "web_extract", "get_file_info",
    "check_command_exists", "get_system_info", "get_user_input",
})

# Write tools: never tracked for no-progress (they always change state)
MUTATING_TOOLS: FrozenSet[str] = frozenset({
    "write_file", "edit_file", "bash", "delete_file",
    "create_directory", "move_file", "copy_file",
    "execute_code", "send_message", "add_cron_job",
    "load_skill", "manage_note",
})


# ---------------------------------------------------------------------------
# Signature Hashing
# ---------------------------------------------------------------------------

def _canonical_tool_args(args: Dict[str, Any]) -> str:
    """Produce a sorted, compact JSON representation of tool arguments."""
    return json.dumps(
        args, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )


def _sha256(value: str) -> str:
    """SHA-256 hash of a string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_hash(result: Any) -> str:
    """Produce a hash of a tool result for no-progress detection."""
    if isinstance(result, str):
        return _sha256(result)
    return _sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))


@dataclass(frozen=True)
class ToolCallSignature:
    """Unique signature for a tool call: tool_name + SHA-256 of canonical args."""
    
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Optional[Dict[str, Any]]) -> ToolCallSignature:
        """Create a signature from a tool call."""
        canonical = _canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Configurable thresholds for tool-call guardrails.

    Loaded from config.yaml section ``tool_loop_guardrails`` or defaults.
    """
    
    # Enable/disable warning messages (soft nudge)
    warnings_enabled: bool = True
    
    # Enable/disable hard stops (circuit breaker)
    hard_stop_enabled: bool = True
    
    # --- Exact call repetition (same tool + same args) ---
    exact_failure_warn_after: int = 2     # Warn after 2 consecutive failures
    exact_failure_block_after: int = 5    # Block after 5 consecutive failures
    
    # --- Same-tool failures (same tool, any args) ---
    same_tool_failure_warn_after: int = 3   # Warn after 3 failures
    same_tool_failure_halt_after: int = 8   # Halt after 8 failures
    
    # --- No-progress (idempotent tools returning same result) ---
    no_progress_warn_after: int = 2     # Warn after 2 identical results
    no_progress_block_after: int = 5    # Block after 5 identical results
    
    # Tool classification
    idempotent_tools: FrozenSet[str] = field(default_factory=lambda: IDEMPOTENT_TOOLS)
    mutating_tools: FrozenSet[str] = field(default_factory=lambda: MUTATING_TOOLS)

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> ToolCallGuardrailConfig:
        """Create from a config dict (e.g., from YAML)."""
        if not mapping:
            return cls()
        
        warn = mapping.get("warn_after", {})
        hard_stop = mapping.get("hard_stop_after", {})
        
        return cls(
            warnings_enabled=mapping.get("warnings_enabled", True),
            hard_stop_enabled=mapping.get("hard_stop_enabled", True),
            exact_failure_warn_after=warn.get("exact_failure", 2),
            exact_failure_block_after=hard_stop.get("exact_failure", 5),
            same_tool_failure_warn_after=warn.get("same_tool_failure", 3),
            same_tool_failure_halt_after=hard_stop.get("same_tool_failure", 8),
            no_progress_warn_after=warn.get("idempotent_no_progress", 2),
            no_progress_block_after=hard_stop.get("idempotent_no_progress", 5),
        )


# ---------------------------------------------------------------------------
# Decision Object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolGuardrailDecision:
    """Decision returned by the controller. Runtime enforces it."""
    
    action: str = "allow"          # allow | warn | block | halt
    code: str = ""                 # e.g., "repeated_exact_failure_block"
    message: str = ""              # Human-readable explanation
    tool_name: str = ""            # Which tool triggered this
    count: int = 0                 # How many repetitions observed
    signature: Optional[ToolCallSignature] = None

    @property
    def allows_execution(self) -> bool:
        """True if the tool should be executed."""
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        """True if execution should be halted immediately."""
        return self.action in {"block", "halt"}

    def to_metadata(self) -> Dict[str, Any]:
        """Serialize to dict for inclusion in synthetic results."""
        return {
            "action": self.action,
            "code": self.code,
            "tool_name": self.tool_name,
            "count": self.count,
        }


# ---------------------------------------------------------------------------
# Synthetic Result Formatting
# ---------------------------------------------------------------------------

def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    """Format a synthetic tool result for a blocked/halted tool call.

    This is injected as the tool result so the LLM sees it and can reason
    about why the tool was blocked.
    """
    return json.dumps({
        "error": decision.message,
        "guardrail": decision.to_metadata(),
    }, ensure_ascii=False)


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """Append guardrail guidance to an existing tool result.

    Used for 'warn' decisions — the tool executed but we add guidance
    about the repetition pattern.
    """
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    suffix = (
        f"\n\n[{label}: {decision.code}; count={decision.count}; "
        f"{decision.message}]"
    )
    return (result or "") + suffix


def _tool_failure_recovery_hint(tool_name: str) -> str:
    """Generate a tool-specific recovery hint."""
    if tool_name in ("bash", "execute_code"):
        return (
            "Try a different command or approach. "
            "Check for syntax errors, missing dependencies, or permission issues."
        )
    if tool_name in ("read_file", "list_files", "search_files", "fast_grep", "glob"):
        return (
            "Try a different path, pattern, or search query. "
            "Verify the path exists and is accessible."
        )
    if tool_name in ("write_file", "edit_file"):
        return (
            "Check the file path, permissions, and content format. "
            "Try reading the file first to understand its current state."
        )
    return (
        f"Tool '{tool_name}' is not making progress. "
        "Try a different approach, different arguments, or a different tool."
    )


# ---------------------------------------------------------------------------
# Controller — Side-Effect-Free
# ---------------------------------------------------------------------------

class ToolCallGuardrailController:
    """Tracks tool-call patterns and returns decisions.

    This controller is **side-effect-free** — it only observes and decides.
    The conversation loop owns enforcement (checking decisions, injecting
    synthetic results, halting execution).

    Three tracking patterns per turn:
    1. Exact call repetition (SHA-256 hash of tool_name + args)
    2. Same-tool-any-args failures
    3. Idempotent no-progress (same result hash from read-only tools)

    All counters reset at ``reset_for_turn()``.
    """

    def __init__(self, config: Optional[ToolCallGuardrailConfig] = None):
        self.config = config or ToolCallGuardrailConfig()
        self._exact_failure_counts: Dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: Dict[str, int] = {}
        self._no_progress: Dict[ToolCallSignature, tuple[str, int]] = {}  # (result_hash, repeat_count)
        self._halt_decision: Optional[ToolGuardrailDecision] = None

    def reset_for_turn(self) -> None:
        """Reset all tracking state for a new turn."""
        self._exact_failure_counts.clear()
        self._same_tool_failure_counts.clear()
        self._no_progress.clear()
        self._halt_decision = None

    # ------------------------------------------------------------------
    # Pre-execution check
    # ------------------------------------------------------------------

    def before_call(
        self, tool_name: str, args: Optional[Dict[str, Any]]
    ) -> ToolGuardrailDecision:
        """Check if a tool call should be blocked BEFORE execution.

        Only fires if ``hard_stop_enabled`` is True. Checks:
        1. Exact failure block: has this exact call failed >= threshold?
        2. Idempotent no-progress block: has this read-only call returned
           the same result >= threshold?
        """
        signature = ToolCallSignature.from_call(tool_name, args)

        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        # Check exact failure block
        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            message = (
                f"Tool '{tool_name}' has been called with identical arguments "
                f"{exact_count} times and failed each time. "
                f"{_tool_failure_recovery_hint(tool_name)}"
            )
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=message,
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        # Check idempotent no-progress block
        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None and record[1] >= self.config.no_progress_block_after:
                message = (
                    f"Tool '{tool_name}' has returned the same result "
                    f"{record[1]} times in a row with identical arguments. "
                    f"No progress is being made. "
                    f"{_tool_failure_recovery_hint(tool_name)}"
                )
                decision = ToolGuardrailDecision(
                    action="block",
                    code="idempotent_no_progress_block",
                    message=message,
                    tool_name=tool_name,
                    count=record[1],
                    signature=signature,
                )
                self._halt_decision = decision
                return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    # ------------------------------------------------------------------
    # Post-execution observation
    # ------------------------------------------------------------------

    def after_call(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        result: Optional[str],
        *,
        failed: bool = False,
    ) -> ToolGuardrailDecision:
        """Observe the outcome of a tool call and update tracking state.

        This is where all the counting happens. Called AFTER tool execution.

        Args:
            tool_name: The tool that was called.
            args: The arguments passed to the tool.
            result: The string result of the tool call.
            failed: Whether the tool call failed (error/exception).
        """
        signature = ToolCallSignature.from_call(tool_name, args)

        if failed:
            return self._observe_failure(signature, tool_name)
        else:
            return self._observe_success(signature, tool_name, result)

    def _observe_failure(
        self, signature: ToolCallSignature, tool_name: str
    ) -> ToolGuardrailDecision:
        """Handle a failed tool call."""
        # Increment exact failure count
        self._exact_failure_counts[signature] = (
            self._exact_failure_counts.get(signature, 0) + 1
        )

        # Increment same-tool failure count
        self._same_tool_failure_counts[tool_name] = (
            self._same_tool_failure_counts.get(tool_name, 0) + 1
        )

        # Clear no-progress tracking (failure is not "same result")
        self._no_progress.pop(signature, None)

        exact_count = self._exact_failure_counts[signature]
        same_count = self._same_tool_failure_counts[tool_name]

        # Check halt: same-tool failures exceeded threshold
        if same_count >= self.config.same_tool_failure_halt_after:
            message = (
                f"Tool '{tool_name}' has failed {same_count} times "
                f"with different arguments. The tool itself may be broken "
                f"or unavailable. {_tool_failure_recovery_hint(tool_name)}"
            )
            return ToolGuardrailDecision(
                action="halt",
                code="same_tool_failure_halt",
                message=message,
                tool_name=tool_name,
                count=same_count,
                signature=signature,
            )

        # Check warn: exact failures exceeded threshold
        if exact_count >= self.config.exact_failure_warn_after:
            message = (
                f"Tool '{tool_name}' has been called with identical arguments "
                f"{exact_count} times and failed each time. "
                f"{_tool_failure_recovery_hint(tool_name)}"
            )
            return ToolGuardrailDecision(
                action="warn",
                code="repeated_exact_failure_warn",
                message=message,
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )

        # Check warn: same-tool failures exceeded threshold
        if same_count >= self.config.same_tool_failure_warn_after:
            message = (
                f"Tool '{tool_name}' has failed {same_count} times "
                f"with different arguments. Consider a different approach."
            )
            return ToolGuardrailDecision(
                action="warn",
                code="same_tool_failure_warn",
                message=message,
                tool_name=tool_name,
                count=same_count,
                signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def _observe_success(
        self,
        signature: ToolCallSignature,
        tool_name: str,
        result: Optional[str],
    ) -> ToolGuardrailDecision:
        """Handle a successful tool call."""
        # Clear failure counts (success resets the pattern)
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        # If not idempotent, clear no-progress tracking
        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        # Idempotent tool: track result hash for no-progress detection
        result_hash = _result_hash(result)
        record = self._no_progress.get(signature)

        if record is not None and record[0] == result_hash:
            # Same result as before — increment repeat count
            repeat_count = record[1] + 1
            self._no_progress[signature] = (result_hash, repeat_count)

            if repeat_count >= self.config.no_progress_warn_after:
                message = (
                    f"Tool '{tool_name}' has returned the same result "
                    f"{repeat_count} times in a row with identical arguments. "
                    f"No progress is being made. "
                    f"{_tool_failure_recovery_hint(tool_name)}"
                )
                return ToolGuardrailDecision(
                    action="warn",
                    code="idempotent_no_progress_warn",
                    message=message,
                    tool_name=tool_name,
                    count=repeat_count,
                    signature=signature,
                )
        else:
            # New result — reset repeat count
            self._no_progress[signature] = (result_hash, 1)

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_idempotent(self, tool_name: str) -> bool:
        """Check if a tool is idempotent (read-only)."""
        if tool_name in self.config.idempotent_tools:
            return True
        if tool_name in self.config.mutating_tools:
            return False
        # Unknown tools: assume not idempotent (fail-closed)
        return False

    @property
    def halt_decision(self) -> Optional[ToolGuardrailDecision]:
        """The most recent halt decision, if any. Cleared on reset."""
        return self._halt_decision

    def get_turn_stats(self) -> Dict[str, Any]:
        """Get summary statistics for the current turn."""
        return {
            "exact_failures_tracked": len(self._exact_failure_counts),
            "same_tool_failures_tracked": len(self._same_tool_failure_counts),
            "no_progress_tracked": len(self._no_progress),
            "halt_decision": self._halt_decision is not None,
        }


__all__ = [
    "ToolCallSignature",
    "ToolCallGuardrailConfig",
    "ToolGuardrailDecision",
    "ToolCallGuardrailController",
    "toolguard_synthetic_result",
    "append_toolguard_guidance",
    "IDEMPOTENT_TOOLS",
    "MUTATING_TOOLS",
]
