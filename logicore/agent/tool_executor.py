"""
ToolExecutor: Handles all tool execution logic extracted from Agent.

Consolidates tool dispatch (custom, skill, MCP, internal) into a single
execution path, replacing the dual execution pattern.
"""

import sys
import os
import time
import json
import asyncio
import inspect
from enum import Enum
from typing import Dict, Any, Callable, List, Optional
import logging

from logicore.tools import execute_tool, SAFE_TOOLS
from logicore.tools.dedup import result_cache, semantic_analyzer, hash_tool_call
from logicore.tools.tool_names import ToolName

logger = logging.getLogger(__name__)


class ApprovalDecision(Enum):
    """
    Return value contract for an ``on_tool_approval`` callback.

    Callbacks may return:
    - ``ApprovalDecision.DENY``          -> block this call
    - ``ApprovalDecision.ALLOW_ONCE``   -> allow this call only (re-prompt next time)
    - ``ApprovalDecision.ALLOW_SESSION`` -> allow and cache for the rest of the session

    For backward compatibility a callback may also return a plain ``bool``
    (``True`` == ALLOW_ONCE, ``False`` == DENY) or a ``dict`` of mutated args
    (interpreted as ALLOW_ONCE with the returned args substituted).
    """

    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"


class PermissionMode(Enum):
    """
    Permission modes controlling tool approval behavior.

    Consolidates the permission concepts from the former permissions.py
    module into the ToolExecutor, which already owns the approval pipeline.

    - DEFAULT: Defer to tool-specific permissions (SAFE_TOOLS auto-allow,
      everything else prompts for approval).
    - AUTO: Auto-approve all tool calls (no prompts).
    - PLAN: Read-only mode — only tools that pass ``is_read_only()`` are
      allowed; everything else is denied.
    - BYPASS: Bypass all permission checks (dangerous, for trusted contexts).
    """

    DEFAULT = "default"
    AUTO = "auto"
    PLAN = "plan"
    BYPASS = "bypass"


# Tools that share a single trust boundary: they all send data to a
# third-party API (network egress). Approving one of them for a session
# implies consent for the others, so they are gated by a single
# per-session decision rather than a per-call prompt.
_NETWORK_EGRESS_TOOLS = {
    ToolName.WEB_SEARCH,
    ToolName.IMAGE_SEARCH,
    ToolName.URL_FETCH,
}

# Tools that must NEVER be session-cached as approved. These are
# destructive/mutating and must re-prompt on every invocation regardless
# of any earlier decision in the session.
_NON_CACHABLE_TOOLS = {
    ToolName.DELETE_FILE,
    ToolName.EXECUTE_COMMAND,
    ToolName.GIT_COMMAND,
    ToolName.CODE_EXECUTE
}


class ToolExecutor:
    """
    Unified tool execution engine.
    
    Consolidates execution of:
    - Custom tools (user-provided executors)
    - Skill tools (from loaded skills)
    - MCP tools (from connected MCP servers)
    - Internal tools (built-in logicore tools)
    
    Also handles:
    - Tool approval checks
    - Result caching and deduplication
    - Auto-heal for common tool errors
    """
    
    def __init__(self, debug: bool = False, approval_timeout: Optional[float] = None,
                 allow_tools: Optional[set] = None):
        self.debug = debug
        
        # Tool registries
        self.custom_tool_executors: Dict[str, Callable] = {}
        self.skill_tool_executors: Dict[str, Callable] = {}
        self.mcp_managers: List[Any] = []  # MCPClientManager instances
        
        # Approval settings
        self.auto_approve_all = False
        # Permission mode (consolidated from former permissions.py)
        self.permission_mode = PermissionMode.DEFAULT
        self._pre_plan_mode: Optional[PermissionMode] = None
        # Set of tool names that are pre-approved and skip the approval check
        # entirely.  Useful for delegating authority to child processes: the
        # parent pre-authorises specific tools, the child picks them up and
        # can execute them without prompting.
        if allow_tools is not None:
            self.allow_tools: set = set(allow_tools)
        else:
            # Fall back to env var so child subprocesses inherit the parent's
            # allow-list without explicit wiring.
            _env_allow = os.environ.get("LOGICORE_ALLOW_TOOLS")
            if _env_allow:
                try:
                    self.allow_tools = set(json.loads(_env_allow))
                except (json.JSONDecodeError, TypeError):
                    self.allow_tools = set()
            else:
                self.allow_tools = set()
        # Maximum seconds to wait for an approval decision before returning a
        # structured "needs_approval" result.  ``None`` means wait forever
        # (backward-compatible default for interactive sessions).
        self.approval_timeout: Optional[float] = approval_timeout

        # Propagate to child processes: when a bash/execute_command tool spawns
        # a subprocess that creates its own Agent, the child picks this up via
        # os.environ so its ToolExecutor also gets a timeout instead of hanging
        # on input() forever.
        if approval_timeout is not None:
            os.environ["LOGICORE_APPROVAL_TIMEOUT"] = str(approval_timeout)

        # Session-scoped approval cache: session_id -> {group_key: decision(bool)}.
        # Lets a single per-session grant (e.g. "allow internet access") cover
        # all subsequent calls in that session without re-prompting.
        self._approval_cache: Dict[str, Dict[str, bool]] = {}

        # Callbacks
        self.callbacks = {
            "on_tool_start": None,
            "on_tool_end": None,
            "on_tool_approval": None,
        }

        # Names of tools deferred by the last budget assembly (for discovery).
        self.last_deferred_tools: List[str] = []
    
    def register_custom_tool(self, name: str, executor: Callable):
        """Register a custom tool executor."""
        self.custom_tool_executors[name] = executor
    
    def register_skill_tool(self, name: str, executor: Callable):
        """Register a skill tool executor."""
        self.skill_tool_executors[name] = executor
    
    def unregister_skill_tool(self, name: str):
        """Unregister a skill tool executor."""
        self.skill_tool_executors.pop(name, None)
    
    def add_mcp_manager(self, manager):
        """Add an MCP client manager."""
        self.mcp_managers.append(manager)
    
    def set_auto_approve(self, enabled: bool):
        """Enable/disable auto-approval for all tools."""
        self.auto_approve_all = enabled

    def set_permission_mode(self, mode: PermissionMode):
        """Set the permission mode."""
        self.permission_mode = mode

    def get_permission_mode(self) -> PermissionMode:
        """Get the current permission mode."""
        return self.permission_mode

    def enter_plan_mode(self):
        """Enter plan mode (read-only, except plan file writes).

        Stashes the current mode so it can be restored via exit_plan_mode().
        """
        if self.permission_mode != PermissionMode.PLAN:
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = PermissionMode.PLAN

    def exit_plan_mode(self):
        """Exit plan mode and restore the previous permission mode."""
        if self.permission_mode == PermissionMode.PLAN and self._pre_plan_mode is not None:
            self.permission_mode = self._pre_plan_mode
            self._pre_plan_mode = None
        elif self.permission_mode == PermissionMode.PLAN:
            self.permission_mode = PermissionMode.AUTO

    def is_read_only_mode(self) -> bool:
        """Check if currently in read-only (plan) mode."""
        return self.permission_mode == PermissionMode.PLAN

    def set_allow_tools(self, tools: set):
        """Set tools that are pre-approved and skip the approval check.

        Also propagates to ``LOGICORE_ALLOW_TOOLS`` env var so child
        subprocesses (e.g. bash running a script that creates its own Agent)
        inherit the same allow-list.
        """
        self.allow_tools = set(tools) if tools else set()
        if self.allow_tools:
            os.environ["LOGICORE_ALLOW_TOOLS"] = json.dumps(sorted(self.allow_tools))
        else:
            os.environ.pop("LOGICORE_ALLOW_TOOLS", None)
    
    def set_callbacks(self, **kwargs):
        """Set execution callbacks."""
        self.callbacks.update(kwargs)
    
    @staticmethod
    def _normalize_approval(result, args: Dict[str, Any]) -> tuple:
        """
        Normalize a callback return value into (approved, approve_all, args).

        - ``ApprovalDecision`` -> explicit decision (+ session grant flag)
        - ``bool``             -> True == allow once, False == deny
        - ``dict``             -> allow once with the dict substituted as args
        - anything else        -> truthiness used as allow-once
        """
        if isinstance(result, ApprovalDecision):
            if result is ApprovalDecision.DENY:
                return False, False, args
            if result is ApprovalDecision.ALLOW_SESSION:
                return True, True, args
            return True, False, args
        if isinstance(result, bool):
            return result, False, args
        if isinstance(result, dict):
            # Backward-compatible: a dict means "approved, use these args".
            return True, False, result
        return bool(result), False, args

    @staticmethod
    def _needs_approval_result(
        tool_name: str,
        tool_args: Dict[str, Any],
        reason: str,
        timed_out: bool = False,
    ) -> Dict[str, Any]:
        """Return a structured result when approval cannot be obtained.

        The result is shaped so that the supervising agent (LLM) can reason
        about *why* the tool call did not execute and report back to the user.
        Key fields:

        - ``needs_approval``: always ``True`` — signals that the tool requires
          a human approval that was not granted.
        - ``timed_out``: ``True`` when the approval window expired (vs. an
          immediate deny from a headless guard).
        - ``approval_timeout_seconds``: the configured timeout value (``None``
          means "no timeout was configured, but approval still wasn't received",
          which can happen in headless contexts).
        """
        return {
            "success": False,
            "error": reason,
            "needs_approval": True,
            "timed_out": timed_out,
            "tool_name": tool_name,
            "tool_args": tool_args,
        }

    async def _default_approval_callback(
        self,
        session_id: str,
        tool_name: str,
        args: Dict[str, Any],
        group: Optional[str] = None,
    ) -> ApprovalDecision:
        """
        Default approval callback that prompts user in terminal.

        Used when no on_tool_approval callback is configured. Offers three
        choices:

        - ``y``  allow this single call (re-prompt next time)
        - ``a``  allow and remember for the rest of the session (no re-prompt)
        - ``n``  deny

        When ``group`` is the network-egress group the grant is inherently
        session-scoped, so ``y`` maps to a session grant (it will not ask
        again this session) and only yes/no is offered.
        """
        # Headless guard: in autonomous / non-interactive contexts there is no
        # human to answer the prompt.  Calling input() here would block forever
        # (or raise EOFError on a closed pipe).  Fail-fast with DENY so the
        # supervising agent gets a clear "Denied by user" result instead of
        # hanging indefinitely.
        if not sys.stdin.isatty():
            logger.info(
                f"[ToolExecutor] Headless mode detected — auto-denying approval for '{tool_name}'"
            )
            return ApprovalDecision.DENY

        args_preview = json.dumps(args, default=str)[:200] if args else "{}"

        if group == "network_egress":
            print(f"\n{'='*60}")
            print(f"INTERNET ACCESS REQUESTED (session: {session_id})")
            print(f"{'='*60}")
            print(f"The agent wants to use web tools (e.g. `{tool_name}`) which send")
            print("your query to a third-party API. Allow this for the rest of the")
            print("current session? It will not ask again this session.")
            print(f"Args: {args_preview}")
            print(f"{'='*60}")
            prompt = "Allow internet access for this session? (yes/no): "
            while True:
                try:
                    response = input(prompt).strip().lower()
                    if response in ('yes', 'y'):
                        return ApprovalDecision.ALLOW_SESSION
                    elif response in ('no', 'n'):
                        return ApprovalDecision.DENY
                    else:
                        print("Please enter 'yes' or 'no'")
                except (EOFError, KeyboardInterrupt):
                    print("\nApproval denied (no input)")
                    return ApprovalDecision.DENY

        print(f"\n{'='*60}")
        print("TOOL APPROVAL REQUIRED")
        print(f"{'='*60}")
        print(f"Tool: {tool_name}")
        print(f"Args: {args_preview}")
        print("  [y] Yes            - allow just this call")
        print("  [a] Yes, this session - allow and don't ask again this session")
        print("  [n] No             - deny")
        print(f"{'='*60}")
        prompt = "Approve? (y/a/n): "

        while True:
            try:
                response = input(prompt).strip().lower()
                if response in ('yes', 'y'):
                    return ApprovalDecision.ALLOW_ONCE
                elif response in ('all', 'a'):
                    return ApprovalDecision.ALLOW_SESSION
                elif response in ('no', 'n'):
                    return ApprovalDecision.DENY
                else:
                    print("Please enter 'y', 'a', or 'n'")
            except (EOFError, KeyboardInterrupt):
                print("\nApproval denied (no input)")
                return ApprovalDecision.DENY

    def _approval_group(self, name: str) -> Optional[str]:
        """
        Return the session-cache group for a tool, or None if the tool must
        re-prompt on every call.

        Network-egress tools share one group so a single per-session decision
        covers all of them. Destructive/mutating tools return None and are
        never session-cached.
        """
        if name in _NON_CACHABLE_TOOLS:
            return None
        if name in _NETWORK_EGRESS_TOOLS:
            return "network_egress"
        return None

    def clear_session_approvals(self, session_id: str) -> None:
        """Forget any cached approval decisions for a session."""
        self._approval_cache.pop(session_id, None)
    
    def requires_approval(self, name: str) -> bool:
        """Check if a tool requires user approval.

        Respects the current permission mode:
        - BYPASS/AUTO: nothing requires approval
        - PLAN: nothing requires approval here (denial is handled separately)
        - DEFAULT: defers to SAFE_TOOLS and allow_tools sets
        """
        if self.permission_mode in (PermissionMode.BYPASS, PermissionMode.AUTO):
            return False
        if self.auto_approve_all:
            return False
        if name in self.allow_tools:
            return False
        if name in SAFE_TOOLS:
            return False
        if name == 'computer':
            return False
        return True
    
    def get_registered_tool_names(self) -> set:
        """Return the set of tool names that can actually be executed.

        Covers custom tools, skill tools, MCP tools, and internal registry
        tools. Used by the prompt-verification gate to detect "phantom" tools
        that are documented in the system prompt but have no executor.
        """
        names = set(self.custom_tool_executors) | set(self.skill_tool_executors)
        try:
            from logicore.tools.registry import registry as _global_registry
            names |= set(_global_registry.tool_names)
        except Exception:
            pass
        for manager in self.mcp_managers:
            if hasattr(manager, "server_tools_map"):
                names |= set(manager.server_tools_map.keys())
        return names

    def parse_tool_arguments(self, name: str, raw_args: Any) -> tuple[Dict[str, Any], Optional[str]]:
        """Parse tool-call arguments into a JSON object."""
        if raw_args is None:
            return {}, None
        if isinstance(raw_args, dict):
            return raw_args, None
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                return {}, f"Tool '{name}' received invalid JSON arguments: {str(exc)}"
            if not isinstance(parsed, dict):
                return {}, f"Tool '{name}' arguments must be a JSON object, got {type(parsed).__name__}."
            return parsed, None
        return {}, f"Tool '{name}' arguments must be a dict or JSON object string, got {type(raw_args).__name__}."
    
    def normalize_tool_result(self, tool_name: str, result: Any) -> Dict[str, Any]:
        """Normalize all tool outputs to a canonical envelope."""
        if isinstance(result, dict):
            if "success" in result:
                normalized = {"success": bool(result.get("success"))}
                if "content" in result:
                    normalized["content"] = result.get("content")
                if "error" in result and result.get("error") is not None:
                    normalized["error"] = str(result.get("error"))
                elif not normalized["success"]:
                    exception_text = result.get("exception")
                    normalized["error"] = str(exception_text) if exception_text else f"Tool '{tool_name}' failed without an explicit error message."
                if normalized["success"] and "content" not in normalized and "message" in result:
                    normalized["content"] = result.get("message")
                return normalized
            if result.get("error") is not None or result.get("exception") is not None:
                return {"success": False, "error": str(result.get("error") or result.get("exception"))}
            if "content" in result:
                return {"success": True, "content": result.get("content")}
            return {"success": True, "content": result}
        return {"success": True, "content": result}
    
    def compute_tool_signature(self, name: str, args: Dict[str, Any]) -> str:
        """Create a stable SHA-256 signature for tool deduplication."""
        return hash_tool_call(name, args)
    
    async def execute(
        self,
        name: str,
        args: Dict[str, Any],
        session_id: str,
        local_result_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a tool with all layers: cache check, approval, dispatch.
        
        Returns normalized tool result.
        """
        signature = self.compute_tool_signature(name, args)
        result = None
        reused_cached_result = False

        if self.debug:
            args_preview = json.dumps(args, default=str)[:300] if args else "{}"
            logger.debug(f"[ToolExecutor] ▶ execute '{name}' args={args_preview}")
        
        # Plan mode: deny non-read-only tools (SAFE_TOOLS are read-only)
        if self.permission_mode == PermissionMode.PLAN and name not in SAFE_TOOLS:
            return self.normalize_tool_result(
                name, {"success": False, "error": f"Write operations blocked in plan mode: '{name}'"}
            )
        
        # Check approval
        approved = True
        if self.requires_approval(name):
            group = self._approval_group(name)
            # Network-egress tools share a group key; everything else is keyed
            # per-tool so a "yes, this session" grant only covers that tool.
            cache_key = group if group is not None else f"tool:{name}"
            session_cache = self._approval_cache.get(session_id)
            cached = session_cache.get(cache_key) if session_cache is not None else None

            if cached is not None:
                # Reuse the per-session decision — no re-prompt.
                approved = cached
                if self.debug:
                    logger.debug(
                        f"[ToolExecutor] Reusing session approval={approved} for '{cache_key}'"
                    )
            elif self.callbacks.get("on_tool_approval"):
                try:
                    if self.approval_timeout is not None:
                        approval_result = await asyncio.wait_for(
                            self.callbacks["on_tool_approval"](session_id, name, args),
                            timeout=self.approval_timeout,
                        )
                    else:
                        approval_result = await self.callbacks["on_tool_approval"](session_id, name, args)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[ToolExecutor] Approval timed out ({self.approval_timeout}s) for '{name}'"
                    )
                    return self._needs_approval_result(
                        name, args,
                        reason=(
                            f"Approval timed out for tool '{name}' after {self.approval_timeout}s. "
                            f"The tool call requires user approval but no decision was received in time."
                        ),
                        timed_out=True,
                    )
                approved, approve_all, args = self._normalize_approval(approval_result, args)
                # Cache when: a grouped decision was made (incl. a cached
                # denial, so the network group keeps denying silently), or the
                # user explicitly granted the tool for the whole session.
                # Non-cacheable tools (destructive) must never be session-cached.
                if (group is not None or (approved and approve_all)) and name not in _NON_CACHABLE_TOOLS:
                    self._approval_cache.setdefault(session_id, {})[cache_key] = approved
            else:
                # No approval callback configured - use default terminal prompt
                try:
                    if self.approval_timeout is not None:
                        approval_result = await asyncio.wait_for(
                            self._default_approval_callback(session_id, name, args, group),
                            timeout=self.approval_timeout,
                        )
                    else:
                        approval_result = await self._default_approval_callback(session_id, name, args, group)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[ToolExecutor] Approval timed out ({self.approval_timeout}s) for '{name}'"
                    )
                    return self._needs_approval_result(
                        name, args,
                        reason=(
                            f"Approval timed out for tool '{name}' after {self.approval_timeout}s. "
                            f"The tool call requires user approval but no decision was received in time."
                        ),
                        timed_out=True,
                    )
                approved, approve_all, _ = self._normalize_approval(approval_result, args)
                if (group is not None or (approved and approve_all)) and name not in _NON_CACHABLE_TOOLS:
                    self._approval_cache.setdefault(session_id, {})[cache_key] = approved
        
        if not approved:
            return result or self.normalize_tool_result(name, {"success": False, "error": "Denied by user"})
        
        # Skip cache for stateful/dynamic tools
        if name in _NON_CACHABLE_TOOLS:
            reused_cached_result = False
        else:
            # Check persistent cache
            cached_result = result_cache.get(signature)
            reused_cached_result = bool(cached_result and cached_result.get("success"))
            
            # Semantic fallback
            if not reused_cached_result and local_result_cache is not None:
                semantic_key = semantic_analyzer.get_semantic_key(name, args)
                if semantic_key:
                    for cached_sig, cached_val in local_result_cache.items():
                        if (
                            cached_val.get("success")
                            and semantic_analyzer.get_semantic_key(name, cached_val.get("_args", {})) == semantic_key
                        ):
                            cached_result = cached_val
                            reused_cached_result = True
                            if self.debug:
                                logger.debug(f"[ToolExecutor] Semantic dedup: '{name}' matches earlier call")
                            break
        
        if reused_cached_result:
            return cached_result
        
        # Execute tool
        start_time = time.time()
        try:
            result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
            duration_ms = (time.time() - start_time) * 1000

            # Structured tool-error recovery (mirrors the hermes-agent parent's
            # error_classifier failover path). Uses the RecoveryAction enum to
            # determine the right recovery strategy for each error type.
            if isinstance(result, dict) and not result.get("success", True):
                from logicore.tools.error_classifier import classify_tool_error, RecoveryAction
                classified = classify_tool_error(
                    Exception(result.get("error", "Unknown error")),
                    tool_name=name,
                    is_credentialed=self._has_credential_backend(name),
                )
                
                # Apply recovery based on the classified action
                if classified.recovery_action == RecoveryAction.ROTATE_CREDENTIAL:
                    if self._rotate_credentials(name):
                        logger.info(f"[ToolExecutor] Rotated credentials, retrying '{name}'")
                        result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
                        duration_ms = (time.time() - start_time) * 1000
                
                elif classified.recovery_action == RecoveryAction.RETRY_SAME:
                    # Transient error — single retry with backoff
                    if classified.should_backoff:
                        logger.info(f"[ToolExecutor] Retrying '{name}' (transient error)")
                        await asyncio.sleep(0.5)  # Brief backoff
                        result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
                        duration_ms = (time.time() - start_time) * 1000
                
                elif classified.recovery_action == RecoveryAction.BACKOFF_AND_RETRY:
                    # Transient server error / rate limit — backoff then retry once
                    # Use retry_after_seconds from classifier, with status-code-aware defaults
                    backoff_time = classified.error_context.get("retry_after_seconds")
                    if backoff_time is None:
                        # Status-code-aware defaults: 429 gets longer backoff
                        if classified.status_code == 429:
                            backoff_time = 5.0
                        elif classified.status_code and classified.status_code >= 500:
                            backoff_time = 1.0
                        else:
                            backoff_time = 1.0
                    backoff_time = float(backoff_time)
                    logger.info(f"[ToolExecutor] Backing off {backoff_time}s then retrying '{name}' (transient error)")
                    await asyncio.sleep(backoff_time)
                    result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
                    duration_ms = (time.time() - start_time) * 1000
                
                # Store classification metadata in result for downstream consumers
                result["_classification"] = classified.to_dict()

            
            # Auto-heal for edit_file read-before-edit error
            if (
                name == ToolName.EDIT_FILE
                and not bool(result.get("success", True))
                and isinstance(args, dict)
                and args.get("file_path")
                and "file must be read before editing" in str(result.get("error", "")).lower()
            ):
                file_path = args.get("file_path")
                if self.debug:
                    logger.debug(f"[ToolExecutor] Auto-recovery: read_file before edit_file for '{file_path}'")
                _ = self.normalize_tool_result(
                    ToolName.READ_FILE,
                    await self._dispatch(ToolName.READ_FILE, {"file_path": file_path}, session_id),
                )
                _prior_classification = result.get("_classification")
                result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
                if _prior_classification is not None:
                    result["_classification"] = _prior_classification
            
            # Store in caches
            if name not in _NON_CACHABLE_TOOLS:
                result_cache.set(signature, result)
            if local_result_cache is not None:
                local_result_cache[signature] = {**result, "_args": args}

            if self.debug:
                ok = bool(result.get("success", True))
                preview = str(result.get("content") or result.get("error") or "")[:200]
                logger.debug(
                    f"[ToolExecutor] ◀ '{name}' {'OK' if ok else 'FAILED'} "
                    f"({duration_ms:.0f}ms): {preview}"
                )

            return result
            
        except Exception as e:
            logger.error(f"Tool execution failed: {name} | Error: {e}")
            return {"success": False, "error": f"Tool execution failed: {str(e)}"}
    
    async def _dispatch(self, name: str, args: Dict[str, Any], session_id: str) -> Any:
        """Dispatch tool execution to the appropriate handler."""
        # 1. Custom tools
        if name in self.custom_tool_executors:
            executor = self.custom_tool_executors[name]
            if inspect.iscoroutinefunction(executor):
                return await executor(**args)
            else:
                return executor(**args)
        
        # 2. Skill tools
        if name in self.skill_tool_executors:
            executor = self.skill_tool_executors[name]
            if inspect.iscoroutinefunction(executor):
                return await executor(**args)
            else:
                return executor(**args)
        
        # 3. MCP tools
        for manager in self.mcp_managers:
            if hasattr(manager, 'server_tools_map') and name in manager.server_tools_map:
                return await manager.execute_tool(name, args)
        
        # 4. Internal tools (fallback)
        return execute_tool(name, args)

    def _has_credential_backend(self, name: str) -> bool:
        """Check if a tool is backed by external credentials (MCP/OAuth).

        Returns True if the tool is managed by an MCP server or OAuth provider
        that could rotate credentials. Used by the error classifier to determine
        whether auth errors should trigger credential rotation.
        """
        for manager in self.mcp_managers:
            if hasattr(manager, "server_tools_map") and name in manager.server_tools_map:
                return True
        return False

    def _rotate_credentials(self, name: str) -> bool:
        """Attempt to rotate credentials for an MCP/OAuth-backed tool.

        Returns True if a rotation was attempted (caller should retry), False
        if the tool has no credentialed backend. Mirrors the parent's
        ``should_rotate_credential`` failover hint for the LLM-call path.
        """
        for manager in self.mcp_managers:
            if hasattr(manager, "server_tools_map") and name in manager.server_tools_map:
                rotate = getattr(manager, "rotate_credentials", None)
                if callable(rotate):
                    try:
                        ok = rotate()
                        if isinstance(ok, bool):
                            return ok
                        return bool(ok)
                    except Exception as e:
                        logger.warning(f"[ToolExecutor] Credential rotation failed for '{name}': {e}")
                        return False
                # No explicit rotate hook — treat as attempted (manager may
                # refresh lazily on next call).
                return True
        return False
    
    async def get_all_tools(
        self,
        internal_tools: List[Dict[str, Any]],
        disabled_tools: set,
        context_length: Optional[int] = None,
        budget_config: Any = None,
    ) -> List[Dict[str, Any]]:
        """Aggregate all tools from all sources, filtering out disabled ones.

        When ``budget_config`` is provided, the aggregated tool set is passed
        through ``logicore.tools.tool_budget.assemble_tool_defs`` so that
        deferrable (MCP/plugin) tools are dropped when their schema cost would
        exceed the per-turn budget — keeping the "narrow waist" of core tools
        always available. Deferred tool names are recorded on
        ``self.last_deferred_tools`` so a discovery bridge can re-surface them.
        """
        seen_names = set()
        filtered_tools = []

        # Internal tools
        for tool in internal_tools:
            name = tool.get("function", {}).get("name")
            if name and name not in disabled_tools and f"builtin:{name}" not in disabled_tools:
                if name not in seen_names:
                    seen_names.add(name)
                    filtered_tools.append(tool)

        # MCP tools
        for manager in self.mcp_managers:
            mcp_tools = await manager.get_tools()
            for tool in mcp_tools:
                name = tool.get("function", {}).get("name")
                server_name = "unknown"
                if hasattr(manager, 'server_tools_map'):
                    server_name = manager.server_tools_map.get(name, "unknown")
                if server_name not in disabled_tools and f"mcp_server:{server_name}" not in disabled_tools:
                    tool_id = f"mcp:{server_name}:{name}"
                    if tool_id not in disabled_tools and name not in disabled_tools:
                        if name not in seen_names:
                            seen_names.add(name)
                            filtered_tools.append(tool)

        if budget_config is not None:
            from logicore.tools.tool_budget import assemble_tool_defs
            assembly = assemble_tool_defs(
                filtered_tools, context_length=context_length, config=budget_config,
            )
            # Record deferred names for discovery without recomputing.
            visible_names = {
                (t.get("function") or {}).get("name")
                for t in assembly.tool_defs
                if isinstance((t.get("function") or {}).get("name"), str)
            }
            self.last_deferred_tools = [
                str((t.get("function") or {}).get("name"))
                for t in filtered_tools
                if (t.get("function") or {}).get("name") not in visible_names
            ]
            if assembly.activated and self.debug:
                logger.debug(
                    "[ToolExecutor] tool_budget deferred %d tools (kept %d)",
                    assembly.deferred_count, len(assembly.tool_defs),
                )
            return assembly.tool_defs

        self.last_deferred_tools = []
        return filtered_tools
