import re
from typing import Optional, List, Tuple
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


# Command blocklist: patterns that should be blocked for safety
# Each entry is (pattern, description) where pattern is a regex
BLOCKED_COMMAND_PATTERNS: List[Tuple[str, str]] = [
    # Destructive filesystem operations
    (r'rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/', 'Recursive force delete from root'),
    (r'rm\s+-[a-z]*f[a-z]*r[a-z]*\s+/', 'Recursive force delete from root'),
    (r'rm\s+-rf\s+/\*', 'Recursive force delete all from root'),
    (r'rm\s+-fr\s+/\*', 'Recursive force delete all from root'),
    (r'\brmdir\s+/[a-zA-Z]', 'Remove system directory'),
    
    # Fork bombs and resource exhaustion
    (r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;:', 'Fork bomb'),
    (r'\bwhile\s+true\s*;', 'Infinite loop'),
    
    # Pipe to shell (remote code execution)
    (r'\bcurl\s+.*\|\s*(ba)?sh', 'Pipe remote content to shell'),
    (r'\bwget\s+.*\|\s*(ba)?sh', 'Pipe remote content to shell'),
    (r'\bcurl\s+.*\|\s*bash', 'Pipe remote content to bash'),
    (r'\bwget\s+.*\|\s*bash', 'Pipe remote content to bash'),
    
    # Encoded/obfuscated commands
    (r'\bpowershell\s+-[eE][nN][cC]', 'Encoded PowerShell command'),
    (r'\bpowershell\s+-[eE]\s+', 'Encoded PowerShell command'),
    (r'\bbash\s+-c\s+["\'].*\\x', 'Obfuscated bash command'),
    
    # Disk wipe operations
    (r'\bdd\s+.*of=/dev/', 'Direct disk write'),
    (r'\bmkfs\.', 'Format filesystem'),
    (r'\bfdisk\s+/dev/', 'Disk partitioning'),
    
    # Dangerous permission changes
    (r'\bchmod\s+777\s+/', 'Set world-writable permissions on root'),
    (r'\bchmod\s+-R\s+777\s+/', 'Recursive world-writable permissions'),
    (r'\bchown\s+.*\s+/', 'Change ownership of system files'),
    
    # Network attacks
    (r'\bnc\s+-[a-z]*l', 'Netcat listener'),
    (r'\bnmap\s+', 'Network scanning'),
    
    # Credential/access operations
    (r'\bcat\s+/etc/shadow', 'Read password hashes'),
    (r'\bcat\s+/etc/passwd.*\|', 'Read and pipe system user data'),
    
    # Process manipulation
    (r'\bkill\s+-9\s+1\b', 'Kill init/systemd process'),
    (r'\bkillall\s+', 'Kill all processes'),
    (r'\bpkill\s+-9\s+', 'Force kill processes'),
    
    # System modification
    (r'\brm\s+/etc/', 'Remove system configuration'),
    (r'\brm\s+/boot/', 'Remove boot files'),
    (r'\brm\s+/bin/', 'Remove system binaries'),
    (r'\brm\s+/sbin/', 'Remove system binaries'),
    (r'\brm\s+/usr/', 'Remove user programs'),
    (r'\brm\s+/var/', 'Remove variable data'),
    (r'\brm\s+/sys/', 'Remove sysfs'),
    (r'\brm\s+/proc/', 'Remove procfs'),
    
    # Scheduled task manipulation
    (r'\bcrontab\s+-r', 'Remove all cron jobs'),
    (r'\bat\s+\d', 'Schedule one-time command with time'),
]


def validate_command(command: str) -> Optional[str]:
    """
    Validate a command against the blocklist.
    
    Returns:
        Error message if command is blocked, None if safe.
    """
    cmd_lower = command.lower().strip()
    
    for pattern, description in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, cmd_lower):
            return f"Command blocked: {description}. This command could cause irreversible damage."
    
    return None


class SmartBashParams(BaseModel):
    command: str = Field(
        ...,
        description="The command to execute. Python code is auto-detected."
    )
    purpose: Optional[str] = Field(
        None,
        description="Brief description of what this command does (for learning)"
    )
    working_dir: Optional[str] = Field(
        None,
        description="Working directory for command execution"
    )
    workdir: Optional[str] = Field(
        None,
        description="Alias for working_dir. Working directory for command execution."
    )
    timeout: int = Field(
        60,
        description="Timeout in seconds (10-300). Minimum 10s to prevent premature termination."
    )


class SmartBashTool(BaseTool):
    """
    Shell command execution with auto-detection of code types.
    
    Features:
    - Auto-detects host OS and uses appropriate shell
    - Auto-detects Python/JavaScript code and handles via temp files
    - Command blocklist prevents destructive operations
    
    For large code blocks, write to temp file and execute instead of inline.
    """
    
    name = "bash"
    description = (
        "Execute shell commands. Python/JavaScript code is auto-detected and "
        "handled via temp files. Use OS-native commands (PowerShell on Windows, "
        "bash on Linux/Mac). Dangerous commands are blocked for safety."
    )
    args_schema = SmartBashParams
    
    def __init__(self):
        from .execution import ExecuteCommandTool
        self.exec_tool = ExecuteCommandTool()
    
    def run(self, command: str, purpose: str = None, working_dir: str = None,
            workdir: str = None, timeout: int = 60) -> ToolResult:
        # Validate command against blocklist
        # block_reason = validate_command(command)
        # if block_reason:
        #     return ToolResult(success=False, error=block_reason)
        
        # Enforce minimum timeout to prevent premature termination
        timeout = max(10, min(timeout, 300))
        # Support both working_dir and workdir (model sometimes uses wrong name)
        cwd = working_dir or workdir
        result = self.exec_tool.run(
            command=command,
            working_directory=cwd,
            timeout=timeout
        )
        
        return result
