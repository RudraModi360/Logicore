"""
Permission System for tool execution control.

Based on Claude Code's permission system pattern:
- Tools have is_read_only() and is_destructive() properties
- Permission modes: 'auto', 'plan', 'bypassPermissions'
- Read-only tools always allowed
- Destructive tools always ask
- Plan mode blocks writes

Key insight from Claude Code:
- Permission system is in permissions.ts
- Tools have checkPermissions() method
- PermissionResult can be 'allow', 'ask', or 'deny'
- isReadOnly and isDestructive control execution behavior
"""

from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass


class PermissionMode(Enum):
    """Permission modes based on Claude Code's ToolPermissionContext."""
    DEFAULT = "default"  # Initial state - defer to tool-specific permissions
    AUTO = "auto"  # Auto-approve safe operations
    PLAN = "plan"  # Plan mode - read-only except plan file
    BYPASS = "bypassPermissions"  # Bypass all permissions (dangerous)


class PermissionDecision(Enum):
    """Permission decisions for tool execution."""
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionResult:
    """Result of permission check."""
    decision: PermissionDecision
    reason: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None


class PermissionSystem:
    """
    Controls tool execution permissions.
    
    Based on Claude Code's permission system:
    - Read-only tools: always allowed
    - Destructive tools: always ask
    - Plan mode: block writes
    - Auto mode: allow safe operations
    """
    
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT):
        """
        Initialize the permission system.
        
        Args:
            mode: Initial permission mode (DEFAULT for initial state)
        """
        self.mode = mode
        self.pre_plan_mode: Optional[PermissionMode] = None
    
    def check_permission(
        self,
        tool: Any,
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> PermissionResult:
        """
        Check if a tool call is permitted.
        
        Based on Claude Code's checkPermissions() pattern:
        1. Check if tool is read-only (always allow)
        2. Check if tool is destructive (always ask)
        3. Check permission mode
        4. Return decision
        
        Args:
            tool: Tool instance with is_read_only() and is_destructive() methods
            args: Tool arguments
            context: Additional context
        
        Returns:
            PermissionResult with decision
        """
        if context is None:
            context = {}
        
        # Read-only tools: always allow
        if tool.is_read_only(args):
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Read-only operation"
            )
        
        # Destructive tools: always ask
        if tool.is_destructive(args):
            return PermissionResult(
                decision=PermissionDecision.ASK,
                reason="Destructive operation requires confirmation"
            )
        
        # Check permission mode
        if self.mode == PermissionMode.PLAN:
            # Plan mode: block writes (except to plan file)
            plan_file = context.get("plan_file_path")
            target_file = args.get("path", "")
            
            if plan_file and target_file == plan_file:
                # Allow writes to plan file
                return PermissionResult(
                    decision=PermissionDecision.ALLOW,
                    reason="Write to plan file allowed in plan mode"
                )
            
            # Block all other writes
            return PermissionResult(
                decision=PermissionDecision.DENY,
                reason="Write operations blocked in plan mode"
            )
        
        # Default mode: ask for non-read-only tools (more restrictive)
        if self.mode == PermissionMode.DEFAULT:
            return PermissionResult(
                decision=PermissionDecision.ASK,
                reason="Default mode: requires confirmation"
            )
        
        # Auto mode: allow safe operations
        if self.mode == PermissionMode.AUTO:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Auto mode: operation allowed"
            )
        
        # Bypass mode: allow everything
        if self.mode == PermissionMode.BYPASS:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Bypass mode: all operations allowed"
            )
        
        # Default: ask
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason="Default: requires confirmation"
        )
    
    def enter_plan_mode(self):
        """
        Enter plan mode.
        
        Based on Claude Code's prepareContextForPlanMode():
        - Stash current mode for restoration
        - Switch to plan mode
        """
        if self.mode != PermissionMode.PLAN:
            self.pre_plan_mode = self.mode
            self.mode = PermissionMode.PLAN
    
    def exit_plan_mode(self):
        """
        Exit plan mode.
        
        Based on Claude Code's pattern:
        - Restore previous mode
        - Clear pre_plan_mode
        """
        if self.mode == PermissionMode.PLAN and self.pre_plan_mode is not None:
            self.mode = self.pre_plan_mode
            self.pre_plan_mode = None
        elif self.mode == PermissionMode.PLAN:
            # No previous mode stored, default to AUTO
            self.mode = PermissionMode.AUTO
    
    def set_mode(self, mode: PermissionMode):
        """Set the permission mode."""
        self.mode = mode
    
    def get_mode(self) -> PermissionMode:
        """Get the current permission mode."""
        return self.mode
    
    def is_read_only_mode(self) -> bool:
        """Check if we're in a read-only mode (plan mode)."""
        return self.mode == PermissionMode.PLAN


class ToolPermissionChecker:
    """
    High-level permission checker for tool execution.
    
    Integrates with ToolExecutor to control tool execution.
    """
    
    def __init__(self, permission_system: Optional[PermissionSystem] = None):
        """
        Initialize the permission checker.
        
        Args:
            permission_system: Permission system instance (creates default if None)
        """
        self.permission_system = permission_system or PermissionSystem()
    
    def should_allow(self, tool: Any, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Check if a tool call should be allowed without asking.
        
        Args:
            tool: Tool instance
            args: Tool arguments
            context: Additional context
        
        Returns:
            True if allowed, False if should ask
        """
        result = self.permission_system.check_permission(tool, args, context)
        return result.decision == PermissionDecision.ALLOW
    
    def should_ask(self, tool: Any, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Check if a tool call should ask for confirmation.
        
        Args:
            tool: Tool instance
            args: Tool arguments
            context: Additional context
        
        Returns:
            True if should ask, False otherwise
        """
        result = self.permission_system.check_permission(tool, args, context)
        return result.decision == PermissionDecision.ASK
    
    def should_deny(self, tool: Any, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Check if a tool call should be denied.
        
        Args:
            tool: Tool instance
            args: Tool arguments
            context: Additional context
        
        Returns:
            True if denied, False otherwise
        """
        result = self.permission_system.check_permission(tool, args, context)
        return result.decision == PermissionDecision.DENY
    
    def check_and_execute(
        self,
        tool: Any,
        args: Dict[str, Any],
        executor: Any,
        context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Check permission and execute if allowed.
        
        Args:
            tool: Tool instance
            args: Tool arguments
            executor: Tool executor function
            context: Additional context
        
        Returns:
            Tool result or permission error
        """
        result = self.permission_system.check_permission(tool, args, context)
        
        if result.decision == PermissionDecision.ALLOW:
            return executor(tool, args)
        
        elif result.decision == PermissionDecision.ASK:
            # In a real implementation, this would ask the user
            # For now, we'll allow it (can be extended with user interaction)
            return executor(tool, args)
        
        elif result.decision == PermissionDecision.DENY:
            return {
                "success": False,
                "error": f"Permission denied: {result.reason}"
            }
        
        return {
            "success": False,
            "error": "Unknown permission decision"
        }


# Global permission system instance
_permission_system: Optional[PermissionSystem] = None
_permission_checker: Optional[ToolPermissionChecker] = None


def get_permission_system() -> PermissionSystem:
    """Get or create the global permission system."""
    global _permission_system
    if _permission_system is None:
        _permission_system = PermissionSystem()
    return _permission_system


def get_permission_checker() -> ToolPermissionChecker:
    """Get or create the global permission checker."""
    global _permission_checker
    if _permission_checker is None:
        _permission_checker = ToolPermissionChecker(get_permission_system())
    return _permission_checker
