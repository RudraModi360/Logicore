import subprocess
import os
import shlex
from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult

# Block dangerous git operations that could cause data loss
BLOCKED_GIT_PATTERNS = [
    'rm -rf', 'rm -r', 'clean -fd', 'clean -fda',
    'push --force', 'push -f', 'reset --hard',
    'branch -D', 'tag -d',
]

class GitCommandParams(BaseModel):
    command: str = Field(..., description='Git command to execute (e.g., "status", "commit -m ...", "log"). Do not include "git" prefix.')
    working_directory: Optional[str] = Field(None, description='Absolute path to repository root.')

class GitCommandTool(BaseTool):
    name = "git_command"
    description = "Execute git commands to manage version control. Always check 'status' before committing."
    args_schema = GitCommandParams

    def _validate_command(self, command: str) -> Optional[str]:
        """Validate git command is safe. Returns error message if blocked, None if ok."""
        cmd_lower = command.lower().strip()
        for pattern in BLOCKED_GIT_PATTERNS:
            if pattern in cmd_lower:
                return f"Blocked: git command contains destructive pattern '{pattern}'. This requires manual execution."
        return None

    def run(self, command: str, working_directory: str = None) -> ToolResult:
        try:
            cwd = os.path.abspath(working_directory) if working_directory else os.getcwd()
            if not os.path.exists(cwd):
                 return ToolResult(success=False, error=f"Working directory does not exist: {cwd}")

            block_reason = self._validate_command(command)
            if block_reason:
                return ToolResult(success=False, error=block_reason)

            # Parse command safely with shell=False
            try:
                cmd_parts = shlex.split(command)
            except ValueError:
                return ToolResult(success=False, error=f"Invalid command syntax: {command}")

            full_args = ["git"] + cmd_parts

            result = subprocess.run(
                full_args,
                shell=False,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=300
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            output_parts = []
            if stdout:
                output_parts.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_parts.append(f"STDERR:\n{stderr}")

            output = "\n".join(output_parts) if output_parts else "(No output)"

            if result.returncode != 0:
                helpful_hint = ""
                if "fatal: not a git repository" in stderr:
                    helpful_hint = "\nHint: Initialize a repo with 'git init' or change directory."
                elif "nothing to commit" in stdout:
                    helpful_hint = "\nHint: Did you forget to 'git add' files?"

                return ToolResult(
                    success=False,
                    content=output,
                    error=f"Git command failed (Exit Code {result.returncode})\n{stderr}{helpful_hint}"
                )

            return ToolResult(success=True, content=output)

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Git command timed out.")
        except Exception as e:
            return ToolResult(success=False, error="Failed to execute git command.")
