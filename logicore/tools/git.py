import subprocess
import os
from typing import Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult

class GitCommandParams(BaseModel):
    command: str = Field(..., description='Git command to execute (e.g., "status", "commit -m ...", "log"). Do not include "git" prefix.')
    working_directory: Optional[str] = Field(None, description='Absolute path to repository root.')

class GitCommandTool(BaseTool):
    name = "git_command"
    description = "Execute git commands to manage version control. Always check 'status' before committing."
    args_schema = GitCommandParams

    def run(self, command: str, working_directory: str = None) -> ToolResult:
        try:
            cwd = os.path.abspath(working_directory) if working_directory else os.getcwd()
            if not os.path.exists(cwd):
                 return ToolResult(success=False, error=f"Working directory does not exist: {cwd}")

            full_command = f"git {command}"
            
            # TODO: Add safety check for destructive commands if needed (reset, etc.)
            
            result = subprocess.run(
                full_command,
                shell=True,
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
                # Add helpful hints for common errors
                helpful_hint = ""
                if "fatal: not a git repository" in stderr:
                    helpful_hint = "\nHint: Initialize a repo with 'git init' or change directory."
                elif "nothing to commit" in stdout: # status sometimes exits 0, but commit exits 1
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
            return ToolResult(success=False, error=f"Failed to execute git command: {e}")
