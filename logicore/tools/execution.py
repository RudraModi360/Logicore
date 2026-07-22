import subprocess
import os
import sys
import platform
import re
import tempfile
import threading
import time
import queue
from typing import Literal, Optional, Dict, List, Any
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from .bash import validate_command


# --- Background Process Manager ---

class BackgroundProcessManager:
    """
    Manages background processes spawned by the agent.
    
    Features:
    - Start processes in background mode
    - Capture stdout/stderr output in real-time
    - View logs/output for any background process
    - Kill processes by ID
    - List all running processes
    """
    
    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}
        self._process_info: Dict[str, Dict[str, Any]] = {}
        self._output_buffers: Dict[str, Dict[str, queue.Queue]] = {}
        self._output_logs: Dict[str, Dict[str, List[str]]] = {}
        self._reader_threads: Dict[str, Dict[str, threading.Thread]] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._max_log_lines = 1000  # Max lines to keep per stream (stdout/stderr)
    
    def _reader_thread(self, proc_id: str, stream_name: str, stream):
        """Background thread to read output from a process stream."""
        try:
            for line in iter(stream.readline, ''):
                if not line:
                    break
                decoded_line = line.decode('utf-8', errors='replace').rstrip('\n\r')
                
                with self._lock:
                    if proc_id in self._output_logs:
                        self._output_logs[proc_id][stream_name].append(decoded_line)
                        # Trim if too many lines
                        if len(self._output_logs[proc_id][stream_name]) > self._max_log_lines:
                            self._output_logs[proc_id][stream_name] = \
                                self._output_logs[proc_id][stream_name][-self._max_log_lines:]
                    
                    # Also put in queue for real-time consumers
                    if proc_id in self._output_buffers and stream_name in self._output_buffers[proc_id]:
                        try:
                            self._output_buffers[proc_id][stream_name].put_nowait(decoded_line)
                        except queue.Full:
                            pass
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
    
    def start_process(self, cmd_list: List[str], cwd: str = None, shell: str = "powershell") -> str:
        """
        Start a process in background and return its ID.
        
        Args:
            cmd_list: Command to execute
            cwd: Working directory
            shell: Shell type used
            
        Returns:
            Process ID string (e.g., "bg_1_1234567890")
        """
        with self._lock:
            self._counter += 1
            proc_id = f"bg_{self._counter}_{int(time.time())}"
        
        try:
            # Start process with CREATE_NEW_PROCESS_GROUP on Windows for proper background execution
            creation_flags = 0
            if platform.system().lower() == 'windows':
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            
            process = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                creationflags=creation_flags if platform.system().lower() == 'windows' else 0,
                start_new_session=platform.system().lower() != 'windows'
            )
            
            with self._lock:
                self._processes[proc_id] = process
                self._process_info[proc_id] = {
                    "pid": process.pid,
                    "command": " ".join(cmd_list) if isinstance(cmd_list, list) else cmd_list,
                    "cwd": cwd,
                    "shell": shell,
                    "started_at": time.time(),
                    "status": "running"
                }
                # Initialize output buffers
                self._output_buffers[proc_id] = {
                    "stdout": queue.Queue(maxsize=10000),
                    "stderr": queue.Queue(maxsize=10000)
                }
                self._output_logs[proc_id] = {
                    "stdout": [],
                    "stderr": []
                }
            
            # Start reader threads for stdout and stderr
            stdout_thread = threading.Thread(
                target=self._reader_thread,
                args=(proc_id, "stdout", process.stdout),
                daemon=True
            )
            stderr_thread = threading.Thread(
                target=self._reader_thread,
                args=(proc_id, "stderr", process.stderr),
                daemon=True
            )
            
            with self._lock:
                self._reader_threads[proc_id] = {
                    "stdout": stdout_thread,
                    "stderr": stderr_thread
                }
            
            stdout_thread.start()
            stderr_thread.start()
            
            return proc_id
            
        except Exception as e:
            return f"error: Failed to start process: {e}"
    
    def get_process(self, proc_id: str) -> subprocess.Popen:
        """Get a background process by ID."""
        with self._lock:
            return self._processes.get(proc_id)
    
    def get_process_info(self, proc_id: str) -> Dict[str, Any]:
        """Get process info by ID."""
        with self._lock:
            info = self._process_info.get(proc_id, {}).copy()
            if proc_id in self._processes:
                process = self._processes[proc_id]
                poll = process.poll()
                if poll is not None:
                    info["status"] = "stopped"
                    info["exit_code"] = poll
                else:
                    info["status"] = "running"
            return info
    
    def get_process_output(self, proc_id: str, stream: str = "both", last_n_lines: int = 50) -> Dict[str, Any]:
        """
        Get output from a background process.
        
        Args:
            proc_id: Process ID
            stream: Which stream to get - "stdout", "stderr", or "both"
            last_n_lines: Number of recent lines to return (0 = all)
            
        Returns:
            Dict with stdout/stderr lines and metadata
        """
        with self._lock:
            if proc_id not in self._output_logs:
                return {"error": f"Process {proc_id} not found"}
            
            info = self._process_info.get(proc_id, {}).copy()
            logs = self._output_logs.get(proc_id, {})
            
            result = {
                "process_id": proc_id,
                "command": info.get("command", "unknown"),
                "status": info.get("status", "unknown"),
                "pid": info.get("pid", "unknown")
            }
            
            if stream in ("stdout", "both"):
                stdout_lines = logs.get("stdout", [])
                if last_n_lines > 0:
                    stdout_lines = stdout_lines[-last_n_lines:]
                result["stdout"] = stdout_lines
                result["stdout_line_count"] = len(logs.get("stdout", []))
            
            if stream in ("stderr", "both"):
                stderr_lines = logs.get("stderr", [])
                if last_n_lines > 0:
                    stderr_lines = stderr_lines[-last_n_lines:]
                result["stderr"] = stderr_lines
                result["stderr_line_count"] = len(logs.get("stderr", []))
            
            return result
    
    def get_live_output(self, proc_id: str, stream: str = "stdout", timeout: float = 1.0) -> Optional[str]:
        """
        Get real-time output from a running process (non-blocking with timeout).
        
        Args:
            proc_id: Process ID
            stream: "stdout" or "stderr"
            timeout: Max seconds to wait for output
            
        Returns:
            Line of output or None if no output available
        """
        with self._lock:
            if proc_id not in self._output_buffers:
                return None
            
            buf = self._output_buffers[proc_id].get(stream)
            if buf is None:
                return None
        
        try:
            return buf.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def tail_output(self, proc_id: str, lines: int = 20) -> str:
        """
        Get the last N lines of combined output (like Unix tail).
        
        Args:
            proc_id: Process ID
            lines: Number of lines to return
            
        Returns:
            Formatted string with recent output
        """
        output = self.get_process_output(proc_id, stream="both", last_n_lines=0)
        if "error" in output:
            return output["error"]
        
        stdout = output.get("stdout", [])
        stderr = output.get("stderr", [])
        
        # Interleave and take last N lines
        combined = []
        for s_line in stdout:
            combined.append(f"[stdout] {s_line}")
        for s_line in stderr:
            combined.append(f"[stderr] {s_line}")
        
        recent = combined[-lines:] if len(combined) > lines else combined
        
        status = output.get("status", "unknown")
        cmd = output.get("command", "unknown")
        
        header = f"=== Process {proc_id} | Status: {status} ==="
        cmd_line = f"Command: {cmd}"
        separator = "=" * 50
        
        if not recent:
            return f"{header}\n{cmd_line}\n{separator}\n(No output yet)"
        
        return f"{header}\n{cmd_line}\n{separator}\n" + "\n".join(recent)
    
    def list_processes(self) -> List[Dict[str, Any]]:
        """List all tracked background processes."""
        with self._lock:
            result = []
            for proc_id, info in self._process_info.items():
                process = self._processes.get(proc_id)
                if process:
                    poll = process.poll()
                    if poll is not None:
                        info["status"] = "stopped"
                        info["exit_code"] = poll
                    else:
                        info["status"] = "running"
                # Add output line counts
                logs = self._output_logs.get(proc_id, {})
                info_copy = info.copy()
                info_copy["stdout_lines"] = len(logs.get("stdout", []))
                info_copy["stderr_lines"] = len(logs.get("stderr", []))
                result.append({"id": proc_id, **info_copy})
            return result
    
    def kill_process(self, proc_id: str) -> bool:
        """Kill a background process by ID."""
        with self._lock:
            process = self._processes.get(proc_id)
            if process:
                try:
                    process.terminate()
                    # Wait briefly then force kill if needed
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    if proc_id in self._process_info:
                        self._process_info[proc_id]["status"] = "killed"
                    return True
                except Exception:
                    return False
            return False
    
    def kill_all(self) -> int:
        """Kill all background processes. Returns count of killed processes."""
        killed = 0
        with self._lock:
            for proc_id in list(self._processes.keys()):
                if self.kill_process(proc_id):
                    killed += 1
        return killed
    
    def cleanup(self):
        """Cleanup all processes."""
        self.kill_all()
        with self._lock:
            self._processes.clear()
            self._process_info.clear()
            self._output_buffers.clear()
            self._output_logs.clear()
            self._reader_threads.clear()


# Global process manager instance
background_manager = BackgroundProcessManager()


# --- Helpers ---

def _detect_host_os() -> str:
    """Detect the host operating system."""
    return platform.system().lower()  # 'windows', 'linux', 'darwin'


def _get_default_shell() -> str:
    """Get the default shell for the current OS."""
    os_name = _detect_host_os()
    if os_name == 'windows':
        return 'powershell'
    return 'bash'





def _is_likely_python_code(command: str) -> bool:
    """
    Heuristic to detect if a command string is actually Python code.
    This avoids the agent having to specify command_type='python'.
    """
    # Strong Python indicators
    python_indicators = [
        'import ', 'from ', 'def ', 'class ', 'print(', 
        'if __name__', 'with open(', 'for ', 'while ',
        'try:', 'except:', 'raise ', 'lambda ',
        'pandas', 'sqlite3', 'numpy', 'os.path',
    ]
    
    # Count indicators
    matches = sum(1 for ind in python_indicators if ind in command)
    
    # If 2+ Python indicators, likely Python code
    if matches >= 2:
        return True
    
    # If starts with common Python patterns
    stripped = command.strip()
    if stripped.startswith(('import ', 'from ', 'def ', 'class ', 'print(')):
        return True
    
    return False


def _is_likely_javascript_code(command: str) -> bool:
    """
    Heuristic to detect if a command string is actually JavaScript/Node.js code.
    Returns True if the command appears to be JavaScript that cannot run directly in bash/PowerShell.
    """
    # JavaScript indicators that cannot run in bash/PowerShell
    js_indicators = [
        'import PptxGenJS',
        'import {',
        'from "pptxgenjs"',
        "from 'pptxgenjs'",
        'const pptx',
        'await pptx.writeFile',
        'require(',
        'console.log(',
        'async function',
        '=> {',
        '.then(',
    ]
    
    # Check for JavaScript-specific patterns
    for indicator in js_indicators:
        if indicator in command:
            return True
    
    # Check for file extensions that suggest JS
    if command.strip().endswith('.js') and ('node ' in command or 'npm ' in command):
        return True
    
    return False


def _get_shell_command(shell: str, command: str, cwd: str = None) -> list:
    """
    Build the subprocess command list for the given shell.
    Returns a list suitable for subprocess.run().
    
    Commands are passed through directly - no translation.
    The system prompt provides OS-specific command guidance.
    """
    if shell == 'powershell':
        # PowerShell: use -Command for inline commands
        if cwd:
            ps_cmd = ['powershell', '-NoProfile', '-Command', f"Set-Location '{cwd}'; {command}"]
        else:
            ps_cmd = ['powershell', '-NoProfile', '-Command', command]
        return ps_cmd
    
    elif shell == 'cmd':
        # CMD: use /c
        cmd_list = ['cmd', '/c', command]
        return cmd_list
    
    else:
        # bash (Linux/macOS)
        if cwd:
            return ['bash', '-c', f"cd '{cwd}' && {command}"]
        return ['bash', '-c', command]


# --- Schemas ---

class ExecuteCommandParams(BaseModel):
    command: str = Field(
        ..., 
        description='Shell command to execute. Python code is auto-detected and handled via temp files.'
    )
    shell: Optional[str] = Field(
        None, 
        description='Shell to use: "powershell", "cmd", "bash". Auto-detected from OS if not specified.'
    )
    working_directory: Optional[str] = Field(
        None, 
        description='Directory where command should run. Defaults to current working directory.'
    )
    workdir: Optional[str] = Field(
        None, 
        description='Alias for working_directory. Directory where command should run.'
    )
    timeout: int = Field(
        300, 
        description='Max execution time in seconds (10-300). Minimum 10s to prevent premature termination.'
    )
    ignore_error: bool = Field(
        False, 
        description='If True, non-zero exit codes will not be treated as failure.'
    )
    background: bool = Field(
        False,
        description='If True, run command in background and return immediately with process ID. Use list_processes/kill_process to manage.'
    )

class ExecuteCodeParams(BaseModel):
    code: str = Field(
        ..., 
        description='The Python code to execute. Must be valid, self-contained Python code.'
    )
    timeout: int = Field(
        60, 
        description='Max execution time in seconds (10-300). Minimum 10s to prevent premature termination.'
    )


# --- Tools ---

class ExecuteCommandTool(BaseTool):
    """
    Execute shell commands on the host OS.
    
    Features:
    - Auto-detects OS and uses appropriate shell (PowerShell on Windows, bash on Linux/macOS)
    - Auto-detects Python/JavaScript code and writes to temp files
    - Commands are passed directly to the shell (no translation)
    
    The agent should use OS-native commands:
    - Windows: PowerShell commands (Get-ChildItem, New-Item, etc.)
    - Linux/Mac: bash commands (ls, mkdir, etc.)
    
    For large code blocks, write to a temp file and execute rather than inline.
    """
    name = "execute_command"
    description = (
        "Execute shell commands on the host OS. "
        "Python/JavaScript code is auto-detected and handled via temp files. "
        "Use OS-native commands (PowerShell on Windows, bash on Linux/Mac)."
    )
    args_schema = ExecuteCommandParams

    def _run_python_code(self, code: str, cwd: str, timeout: int, ignore_error: bool) -> ToolResult:
        """Execute Python code by writing to a temp file."""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix='.py')
            try:
                os.chmod(tmp_path, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
                    tmp.write(code)

                result = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output_parts = []
            if stdout:
                output_parts.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_parts.append(f"STDERR:\n{stderr}")
            output = "\n".join(output_parts) if output_parts else "(No output)"

            if result.returncode == 0 or ignore_error:
                return ToolResult(success=True, content=output)
            else:
                return ToolResult(
                    success=False, 
                    content=output, 
                    error=f"Python execution failed (Exit Code {result.returncode})"
                )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Python code timed out after {timeout}s.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to execute Python code: {e}")

    def _run_javascript_code(self, code: str, cwd: str, timeout: int, ignore_error: bool) -> ToolResult:
        """Execute JavaScript code by writing to a temp file and running with node."""
        try:
            # Check if node is available
            try:
                node_check = subprocess.run(
                    ['node', '--version'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if node_check.returncode != 0:
                    return ToolResult(
                        success=False, 
                        error="Node.js is not installed or not in PATH. Install Node.js to run JavaScript code."
                    )
            except FileNotFoundError:
                return ToolResult(
                    success=False, 
                    error="Node.js is not installed. Install Node.js from https://nodejs.org to run JavaScript code."
                )
            
            # Find project root with node_modules
            project_root = None
            search_dir = cwd or os.getcwd()
            for _ in range(5):  # Search up to 5 levels up
                if os.path.exists(os.path.join(search_dir, 'node_modules')):
                    project_root = search_dir
                    break
                parent = os.path.dirname(search_dir)
                if parent == search_dir:  # Reached root
                    break
                search_dir = parent
            
            # Extract JavaScript code from node -e '...' commands
            js_code = code.strip()
            
            # Handle node -e '...' or node -e "..." syntax
            node_e_match = re.match(r'^node\s+-e\s+["\'](.*)["\']\s*$', js_code, re.DOTALL)
            if node_e_match:
                js_code = node_e_match.group(1)
            
            # Handle node -e with backtick template literals (common in Windows)
            if js_code.startswith("node -e '") or js_code.startswith('node -e "'):
                # Extract content between quotes after node -e
                if js_code.startswith("node -e '"):
                    js_code = js_code[len("node -e '"):]
                    if js_code.endswith("'"):
                        js_code = js_code[:-1]
                elif js_code.startswith('node -e "'):
                    js_code = js_code[len('node -e "'):]
                    if js_code.endswith('"'):
                        js_code = js_code[:-1]
            
            # Write code to temp file
            fd, tmp_path = tempfile.mkstemp(suffix='.js')
            try:
                os.chmod(tmp_path, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
                    tmp.write(js_code)

                # Set up environment with NODE_PATH
                env = os.environ.copy()
                if project_root:
                    node_path = os.path.join(project_root, 'node_modules')
                    env['NODE_PATH'] = node_path
                
                # Run with node
                result = subprocess.run(
                    ['node', tmp_path],
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    env=env,
                    timeout=timeout
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output_parts = []
            if stdout:
                output_parts.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_parts.append(f"STDERR:\n{stderr}")
            output = "\n".join(output_parts) if output_parts else "(No output)"

            if result.returncode == 0 or ignore_error:
                return ToolResult(success=True, content=output)
            else:
                return ToolResult(
                    success=False, 
                    content=output, 
                    error=f"JavaScript execution failed (Exit Code {result.returncode})"
                )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"JavaScript code timed out after {timeout}s.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to execute JavaScript code: {e}")

    def run(
        self, 
        command: str, 
        shell: str = None,
        working_directory: str = None, 
        workdir: str = None,
        timeout: int = 300, 
        ignore_error: bool = False,
        background: bool = False
    ) -> ToolResult:
        try:
            # Enforce minimum timeout to prevent premature termination
            timeout = max(10, min(timeout, 300))
            
            # Validate command against blocklist
            block_reason = validate_command(command)
            if block_reason:
                return ToolResult(success=False, error=block_reason)
            
            # Support both working_directory and workdir
            cwd_path = working_directory or workdir
            # Normalize working directory
            cwd = os.path.abspath(cwd_path) if cwd_path else os.getcwd()
            if not os.path.exists(cwd):
                return ToolResult(success=False, error=f"Working directory does not exist: {cwd}")

            # Determine shell
            if shell is None:
                shell = _get_default_shell()

            # Build shell command
            cmd_list = _get_shell_command(shell, command, cwd)

            # Handle background execution
            if background:
                proc_id = background_manager.start_process(cmd_list, cwd, shell)
                if proc_id.startswith("error:"):
                    return ToolResult(success=False, error=proc_id)
                
                return ToolResult(
                    success=True, 
                    content=f"Process started in background with ID: {proc_id}\n"
                            f"Use 'list_processes' to check status or 'kill_process' to stop.\n"
                            f"PID: {background_manager.get_process_info(proc_id).get('pid', 'unknown')}"
                )

            # Auto-detect JavaScript code and handle via temp file
            if _is_likely_javascript_code(command):
                return self._run_javascript_code(command, cwd, timeout, ignore_error)

            # Auto-detect Python code and handle via temp file
            if _is_likely_python_code(command):
                return self._run_python_code(command, cwd, timeout, ignore_error)

            # Execute
            result = subprocess.run(
                cmd_list,
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

            if result.returncode == 0 or ignore_error:
                return ToolResult(success=True, content=output)
            else:
                return ToolResult(
                    success=False, 
                    content=output, 
                    error=f"Command failed (Exit Code {result.returncode})"
                )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timed out after {timeout} seconds.")
        except FileNotFoundError:
            os_name = _detect_host_os()
            return ToolResult(
                success=False, 
                error=f"Shell '{shell}' not found on {os_name}. Ensure it is installed and in PATH."
            )
        except Exception as e:
            error_msg = f"Failed to execute command: {e}"
            
            # Provide helpful suggestions based on common errors
            error_str = str(e).lower()
            os_name = _detect_host_os()
            
            if 'not recognized' in error_str or 'not found' in error_str:
                if os_name == 'windows':
                    error_msg += "\n\nHint: Use PowerShell commands on Windows:"
                    error_msg += "\n  - New-Item -ItemType Directory -Force -Path 'path'"
                    error_msg += "\n  - Get-ChildItem (not ls)"
                    error_msg += "\n  - Get-Content (not cat)"
                    error_msg += "\n  - Or write code to a .py/.js temp file and execute it"
                else:
                    error_msg += "\n\nHint: Use bash commands on Linux/Mac:"
                    error_msg += "\n  - mkdir -p (not New-Item)"
                    error_msg += "\n  - ls (not Get-ChildItem)"
                    error_msg += "\n  - cat (not Get-Content)"
            
            return ToolResult(success=False, error=error_msg)


class CodeExecuteTool(BaseTool):
    """
    Execute Python code via temp file (avoids shell escaping issues).
    
    Use this tool for:
    - Complex Python scripts
    - Data processing with pandas/numpy
    - Database operations
    - Any code with multiple lines or complex string handling
    
    The code is written to a temp file and executed with the system Python.
    """
    name = "code_execute"
    description = (
        "Execute Python code via temp file. "
        "Use for: complex scripts, data processing, database operations. "
        "Handles escaping automatically."
    )
    args_schema = ExecuteCodeParams

    def run(self, code: str, timeout: int = 60) -> ToolResult:
        try:
            # Enforce minimum timeout to prevent premature termination
            timeout = max(10, min(timeout, 300))
            fd, tmp_path = tempfile.mkstemp(suffix='.py')
            try:
                os.chmod(tmp_path, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as tmp:
                    tmp.write(code)

                result = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
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

                if result.returncode == 0:
                    return ToolResult(success=True, content=output)
                else:
                    return ToolResult(
                        success=False, 
                        content=output, 
                        error=f"Execution failed (Exit Code {result.returncode})"
                    )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Code execution timed out after {timeout}s.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to execute code: {e}")


# --- Process Management Tools ---

class ListProcessesParams(BaseModel):
    pass

class ListProcessesTool(BaseTool):
    """
    List all background processes started by the agent.
    
    Shows process ID, command, status, and other info.
    Use with kill_process to manage background tasks.
    """
    name = "list_processes"
    description = (
        "List all background processes started by the agent. "
        "Shows process ID, command, status, and other info. "
        "Use with kill_process to manage background tasks."
    )
    args_schema = ListProcessesParams

    def run(self) -> ToolResult:
        processes = background_manager.list_processes()
        if not processes:
            return ToolResult(success=True, content="No background processes running.")
        
        lines = ["Background Processes:"]
        for proc in processes:
            status_icon = "🟢" if proc.get("status") == "running" else "🔴"
            lines.append(
                f"{status_icon} ID: {proc['id']} | PID: {proc.get('pid', 'N/A')} | "
                f"Status: {proc.get('status', 'unknown')} | "
                f"Command: {proc.get('command', 'N/A')[:80]}"
            )
        
        return ToolResult(success=True, content="\n".join(lines))


class KillProcessParams(BaseModel):
    process_id: str = Field(
        ..., 
        description='The background process ID to kill (e.g., "bg_1_1234567890")'
    )

class KillProcessTool(BaseTool):
    """
    Kill a background process by its ID.
    
    Use list_processes to get the process IDs first.
    """
    name = "kill_process"
    description = (
        "Kill a background process by its ID. "
        "Use list_processes to get the process IDs first."
    )
    args_schema = KillProcessParams

    def run(self, process_id: str) -> ToolResult:
        success = background_manager.kill_process(process_id)
        if success:
            return ToolResult(success=True, content=f"Process {process_id} killed successfully.")
        else:
            return ToolResult(
                success=False, 
                error=f"Failed to kill process {process_id}. It may not exist or already be stopped."
            )


class GetProcessInfoParams(BaseModel):
    process_id: str = Field(
        ..., 
        description='The background process ID to get info about'
    )

class GetProcessInfoTool(BaseTool):
    """
    Get detailed information about a specific background process.
    
    Use list_processes to get the process IDs first.
    """
    name = "get_process_info"
    description = (
        "Get detailed information about a specific background process. "
        "Use list_processes to get the process IDs first."
    )
    args_schema = GetProcessInfoParams

    def run(self, process_id: str) -> ToolResult:
        info = background_manager.get_process_info(process_id)
        if not info:
            return ToolResult(
                success=False, 
                error=f"Process {process_id} not found."
            )
        
        lines = [f"Process Info for {process_id}:"]
        for key, value in info.items():
            lines.append(f"  {key}: {value}")
        
        return ToolResult(success=True, content="\n".join(lines))


# --- Process Output Viewing Tools ---

class GetProcessOutputParams(BaseModel):
    process_id: str = Field(
        ..., 
        description='The background process ID to get output from'
    )
    stream: str = Field(
        "both",
        description='Which output stream to view: "stdout", "stderr", or "both" (default: "both")'
    )
    last_n_lines: int = Field(
        50,
        description='Number of recent lines to return (0 = all lines). Default: 50'
    )

class GetProcessOutputTool(BaseTool):
    """
    View output (stdout/stderr) from a background process.
    
    Use this to see what a background server or command has printed.
    By default shows the last 50 lines of combined output.
    
    Example use cases:
    - Check server logs after starting uvicorn/flask/fastapi
    - View test output from a background test runner
    - See error messages from a failed background process
    """
    name = "get_process_output"
    description = (
        "View output (stdout/stderr) from a background process. "
        "Use this to see what a background server or command has printed. "
        "By default shows the last 50 lines of combined output."
    )
    args_schema = GetProcessOutputParams

    def run(self, process_id: str, stream: str = "both", last_n_lines: int = 50) -> ToolResult:
        output = background_manager.get_process_output(process_id, stream=stream, last_n_lines=last_n_lines)
        
        if "error" in output:
            return ToolResult(success=False, error=output["error"])
        
        lines = [f"=== Process Output: {process_id} ==="]
        lines.append(f"Command: {output.get('command', 'N/A')}")
        lines.append(f"Status: {output.get('status', 'unknown')}")
        lines.append(f"PID: {output.get('pid', 'N/A')}")
        lines.append("=" * 50)
        
        if stream in ("stdout", "both"):
            stdout = output.get("stdout", [])
            stdout_total = output.get("stdout_line_count", 0)
            lines.append(f"\n--- STDOUT ({len(stdout)} of {stdout_total} lines) ---")
            if stdout:
                lines.extend(stdout)
            else:
                lines.append("(no stdout output)")
        
        if stream in ("stderr", "both"):
            stderr = output.get("stderr", [])
            stderr_total = output.get("stderr_line_count", 0)
            lines.append(f"\n--- STDERR ({len(stderr)} of {stderr_total} lines) ---")
            if stderr:
                lines.extend(stderr)
            else:
                lines.append("(no stderr output)")
        
        return ToolResult(success=True, content="\n".join(lines))


class TailProcessOutputParams(BaseModel):
    process_id: str = Field(
        ..., 
        description='The background process ID to tail output from'
    )
    lines: int = Field(
        20,
        description='Number of recent lines to show (default: 20)'
    )

class TailProcessOutputTool(BaseTool):
    """
    View the last N lines of a background process output (like Unix tail -f).
    
    Quick way to see recent activity of a background process.
    Shows interleaved stdout and stderr with timestamps.
    """
    name = "tail_process_output"
    description = (
        "View the last N lines of a background process output (like Unix tail). "
        "Quick way to see recent activity of a background process."
    )
    args_schema = TailProcessOutputParams

    def run(self, process_id: str, lines: int = 20) -> ToolResult:
        output = background_manager.tail_output(process_id, lines=lines)
        return ToolResult(success=True, content=output)


class WatchProcessParams(BaseModel):
    process_id: str = Field(
        ..., 
        description='The background process ID to watch'
    )
    duration: int = Field(
        5,
        description='Seconds to watch for new output (default: 5)'
    )

class WatchProcessTool(BaseTool):
    """
    Watch live output from a background process for a few seconds.
    
    Similar to 'tail -f' - captures new output as it happens.
    Useful for monitoring a server starting up or watching real-time logs.
    """
    name = "watch_process"
    description = (
        "Watch live output from a background process for a few seconds. "
        "Captures new output as it happens. Good for monitoring startup."
    )
    args_schema = WatchProcessParams

    def run(self, process_id: str, duration: int = 5) -> ToolResult:
        collected_output = []
        
        # Check if process exists
        info = background_manager.get_process_info(process_id)
        if not info:
            return ToolResult(success=False, error=f"Process {process_id} not found")
        
        start_time = time.time()
        lines_found = 0
        
        while time.time() - start_time < duration:
            # Check stdout
            line = background_manager.get_live_output(process_id, "stdout", timeout=0.5)
            if line:
                collected_output.append(f"[stdout] {line}")
                lines_found += 1
                continue
            
            # Check stderr
            line = background_manager.get_live_output(process_id, "stderr", timeout=0.5)
            if line:
                collected_output.append(f"[stderr] {line}")
                lines_found += 1
                continue
            
            # Check if process ended
            current_info = background_manager.get_process_info(process_id)
            if current_info.get("status") in ("stopped", "killed"):
                break
        
        # Also get any buffered output
        output = background_manager.get_process_output(process_id, stream="both", last_n_lines=10)
        
        lines = [f"=== Watching Process: {process_id} ==="]
        lines.append(f"Status: {info.get('status', 'unknown')}")
        lines.append(f"Duration: {duration}s | New lines captured: {lines_found}")
        lines.append("=" * 50)
        
        if collected_output:
            lines.append("\n--- Live Output ---")
            lines.extend(collected_output)
        else:
            lines.append("\n(No new output during watch period)")
        
        # Show last few lines of existing output
        if output.get("stdout") or output.get("stderr"):
            lines.append("\n--- Recent Buffered Output ---")
            for line in output.get("stdout", [])[-5:]:
                lines.append(f"[stdout] {line}")
            for line in output.get("stderr", [])[-5:]:
                lines.append(f"[stderr] {line}")
        
        return ToolResult(success=True, content="\n".join(lines))
