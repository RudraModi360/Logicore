import subprocess
import shlex
import os
import sys
from typing import Literal, Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult

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
                # Attempt to use sys.executable if possible, or just 'python'
                # Escaping quotes for shell execution can be tricky. 
                # It is safer to write to a temp file, but for simplicity we keep -c but try to be safe.
                # However, complex python scripts should be written to file and run via 'python file.py'
                pass 

            # Detect OS for default shell behavior if needed (Windows vs Posix)
            # Python's subprocess.run with shell=True handles this mostly, but 'bash' might not exist on Windows.
            
            # Simple logging or reasoning injection could happen here
            
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
                return ToolResult(
                    success=False, 
                    content=output, 
                    error=f"Command failed (Exit Code {result.returncode})\n{stderr}"
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
