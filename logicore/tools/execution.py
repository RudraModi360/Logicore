import subprocess
import os
import sys
import platform
from typing import Literal, Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult

# OS-aware command mapping
_LINUX_TO_WINDOWS = {
    r'\bgrep\b': 'findstr',
    r'\bls\b': 'dir',
    r'\bcat\b': 'type',
    r'\bwc\b': 'find /c /v',
    r'\bhead\b': 'more',
    r'\btail\b': 'more',
    r'\bchmod\b': 'icacls',
    r'\bcp\b': 'copy',
    r'\brm\b': 'del',
    r'\bmv\b': 'move',
}

# --- Schemas ---

class ExecuteCommandParams(BaseModel):
    command: str = Field(..., description='Shell command to execute. Ensure it is valid for the current OS.')
    command_type: Literal['bash', 'powershell', 'cmd', 'python', 'unknown'] = Field('bash', description='Type of shell/command. Use "python" for wrapping python scripts.')
    working_directory: Optional[str] = Field(None, description='Absolute path to directory where command should run.')
    timeout: int = Field(300, description='Max execution time in seconds (1-300). Default is 300.', ge=1, le=300)
    ignore_error: bool = Field(False, description='If True, non-zero exit codes will not verify as failure (useful for grep/diff).')

class ExecuteCodeParams(BaseModel):
    code: str = Field(..., description='The Python code to execute. Must be valid, self-contained python code.')
    timeout: int = Field(60, description='Max execution time in seconds (1-300). Default is 60.', ge=1, le=300)

# --- Tools ---

class ExecuteCommandTool(BaseTool):
    name = "execute_command"
    description = "Execute shell commands. Use for system operations, installation, or running scripts. PREFER internal file tools for file manipulation."
    args_schema = ExecuteCommandParams

    def run(self, command: str, command_type: str = 'bash', working_directory: str = None, timeout: int = 300, ignore_error: bool = False) -> ToolResult:
        try:
            # Normalize working directory
            cwd = os.path.abspath(working_directory) if working_directory else os.getcwd()
            if not os.path.exists(cwd):
                return ToolResult(success=False, error=f"Working directory does not exist: {cwd}")

            # Safe Python wrapping
            if command_type == 'python':
                pass 

            # OS-aware command adaptation
            if platform.system() == "Windows":
                import re
                original_command = command
                for linux_cmd, win_cmd in _LINUX_TO_WINDOWS.items():
                    command = re.sub(linux_cmd, win_cmd, command)
                if command != original_command:
                    # Log the adaptation for debugging
                    pass
            
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            
            output_parts = []
            if stdout:
                output_parts.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_parts.append(f"STDERR:\n{stderr}")
            
            output = "\n".join(output_parts) if output_parts else "(No output)"
            
            success = result.returncode == 0 or ignore_error
            
            if not success:
                error_msg = f"Command failed (Exit Code {result.returncode})\n{stderr}"
                # Add OS-specific recovery hints
                if platform.system() == "Windows":
                    if "grep" in command.lower() or "'grep'" in command.lower():
                        error_msg += "\n[HINT] On Windows, use 'findstr' instead of 'grep'"
                    elif "'ls'" in command.lower() or "ls " in command.lower():
                        error_msg += "\n[HINT] On Windows, use 'dir' instead of 'ls'"
                    elif "'cat'" in command.lower() or "cat " in command.lower():
                        error_msg += "\n[HINT] On Windows, use 'type' instead of 'cat'"
                return ToolResult(
                    success=False, 
                    content=output, 
                    error=error_msg
                )
            
            return ToolResult(success=True, content=output)

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timed out after {timeout} seconds.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to execute command: {e}")

class CodeExecuteTool(BaseTool):
    name = "code_execute"
    description = "Execute ephemeral Python code. Use for calculations, data processing, or verifying logic. NOT for modifications."
    args_schema = ExecuteCodeParams

    def run(self, code: str, timeout: int = 60) -> ToolResult:
        try:
            # We run python -c "code"
            # This requires careful escaping if we really want to support complex code.
            # A better approach is writing to a temp file.
            import tempfile
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
                tmp.write(code)
                tmp_path = tmp.name
                
            try:
                process = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                
                stdout = process.stdout.strip()
                stderr = process.stderr.strip()
                
                output_parts = []
                if stdout:
                    output_parts.append(f"STDOUT:\n{stdout}")
                if stderr:
                    output_parts.append(f"STDERR:\n{stderr}")
                
                output = "\n".join(output_parts) if output_parts else "(No output)"

                if process.returncode == 0:
                    return ToolResult(success=True, content=output)
                else:
                    return ToolResult(success=False, content=output, error=f"Execution failed (Exit Code {process.returncode})\n{stderr}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Code execution timed out after {timeout}s.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to execute code: {e}")
