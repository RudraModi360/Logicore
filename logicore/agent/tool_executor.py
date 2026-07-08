"""
ToolExecutor: Handles all tool execution logic extracted from Agent.

Consolidates tool dispatch (custom, skill, MCP, internal) into a single
execution path, replacing the dual execution pattern.
"""

import time
import json
import inspect
import asyncio
from typing import Dict, Any, Callable, List, Optional, Union
from datetime import datetime
import logging

from logicore.tools import execute_tool, SAFE_TOOLS
from logicore.tools.dedup import result_cache, semantic_analyzer, hash_tool_call

logger = logging.getLogger(__name__)


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
    
    def __init__(self, debug: bool = False):
        self.debug = debug
        
        # Tool registries
        self.custom_tool_executors: Dict[str, Callable] = {}
        self.skill_tool_executors: Dict[str, Callable] = {}
        self.mcp_managers: List[Any] = []  # MCPClientManager instances
        
        # Approval settings
        self.auto_approve_all = False
        
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
    
    def set_callbacks(self, **kwargs):
        """Set execution callbacks."""
        self.callbacks.update(kwargs)
    
    async def _default_approval_callback(self, session_id: str, tool_name: str, args: Dict[str, Any]) -> bool:
        """
        Default approval callback that prompts user in terminal.
        
        Used when no on_tool_approval callback is configured.
        Prompts user with tool name and args, asks for yes/no approval.
        """
        args_preview = json.dumps(args, default=str)[:200] if args else "{}"
        print(f"\n{'='*60}")
        print(f"TOOL APPROVAL REQUIRED")
        print(f"{'='*60}")
        print(f"Tool: {tool_name}")
        print(f"Args: {args_preview}")
        print(f"{'='*60}")
        
        while True:
            try:
                response = input("Approve? (yes/no): ").strip().lower()
                if response in ('yes', 'y'):
                    return True
                elif response in ('no', 'n'):
                    return False
                else:
                    print("Please enter 'yes' or 'no'")
            except (EOFError, KeyboardInterrupt):
                print("\nApproval denied (no input)")
                return False
    
    def requires_approval(self, name: str) -> bool:
        """Check if a tool requires user approval."""
        if self.auto_approve_all:
            return False
        if name in SAFE_TOOLS:
            return False
        if name == 'computer':
            return False
        return True
    
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
        
        # Check approval
        approved = True
        if self.requires_approval(name):
            if self.callbacks.get("on_tool_approval"):
                approval_result = await self.callbacks["on_tool_approval"](session_id, name, args)
                if isinstance(approval_result, dict):
                    args = approval_result
                    approved = True
                else:
                    approved = bool(approval_result)
            else:
                # No approval callback configured - use default terminal prompt
                approved = await self._default_approval_callback(session_id, name, args)
        
        if not approved:
            return result or self.normalize_tool_result(name, {"success": False, "error": "Denied by user"})
        
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
            # error_classifier failover path). If the result carries a
            # credential failure and this is an MCP/OAuth-backed tool, attempt
            # a single credential rotation + retry before surfacing the error.
            if (
                isinstance(result, dict)
                and not result.get("success", True)
                and result.get("should_rotate_credential")
                and self._rotate_credentials(name)
            ):
                logger.info(f"[ToolExecutor] Rotated credentials, retrying '{name}'")
                result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
                duration_ms = (time.time() - start_time) * 1000

            
            # Auto-heal for edit_file read-before-edit error
            if (
                name == "edit_file"
                and not bool(result.get("success", True))
                and isinstance(args, dict)
                and args.get("file_path")
                and "file must be read before editing" in str(result.get("error", "")).lower()
            ):
                file_path = args.get("file_path")
                if self.debug:
                    logger.debug(f"[ToolExecutor] Auto-recovery: read_file before edit_file for '{file_path}'")
                _ = self.normalize_tool_result(
                    "read_file",
                    await self._dispatch("read_file", {"file_path": file_path}, session_id),
                )
                result = self.normalize_tool_result(name, await self._dispatch(name, args, session_id))
            
            # Store in caches
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
