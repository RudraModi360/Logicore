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


# --- Command Translation for Cross-OS Compatibility ---

def _translate_command_to_powershell(command: str) -> str:
    """
    Translate common Unix/Linux commands to PowerShell equivalents.
    This helps when LLM models generate Unix-style commands on Windows.
    
    Args:
        command: The original command string
        
    Returns:
        Translated command for PowerShell, or original if no translation needed
    """
    cmd = command.strip()
    
    # Direct command translations (order matters - check longer patterns first)
    translations = [
        # mkdir -p <path> → New-Item -ItemType Directory -Force -Path <path>
        (r'^mkdir\s+-p\s+["\']?([^"\']+)["\']?\s*$', r'New-Item -ItemType Directory -Force -Path "\1"'),
        (r'^mkdir\s+["\']?([^"\']+)["\']?\s*$', r'New-Item -ItemType Directory -Force -Path "\1"'),
        
        # rm -rf <path> → Remove-Item -Recurse -Force -Path <path>
        (r'^rm\s+-rf\s+["\']?([^"\']+)["\']?\s*$', r'Remove-Item -Recurse -Force -Path "\1"'),
        (r'^rm\s+-r\s+["\']?([^"\']+)["\']?\s*$', r'Remove-Item -Recurse -Force -Path "\1"'),
        (r'^rm\s+["\']?([^"\']+)["\']?\s*$', r'Remove-Item -Force -Path "\1"'),
        
        # cp -r <src> <dst> → Copy-Item -Recurse -Path <src> -Destination <dst>
        (r'^cp\s+-r\s+["\']?([^"\']+)["\']?\s+["\']?([^"\']+)["\']?\s*$', r'Copy-Item -Recurse -Path "\1" -Destination "\2"'),
        (r'^cp\s+["\']?([^"\']+)["\']?\s+["\']?([^"\']+)["\']?\s*$', r'Copy-Item -Path "\1" -Destination "\2"'),
        
        # mv <src> <dst> → Move-Item -Path <src> -Destination <dst>
        (r'^mv\s+["\']?([^"\']+)["\']?\s+["\']?([^"\']+)["\']?\s*$', r'Move-Item -Path "\1" -Destination "\2"'),
        
        # ls → Get-ChildItem
        (r'^ls\s*$', 'Get-ChildItem'),
        (r'^ls\s+["\']?([^"\']+)["\']?\s*$', r'Get-ChildItem -Path "\1"'),
        
        # pwd → Get-Location
        (r'^pwd\s*$', 'Get-Location'),
        
        # cd <path> → Set-Location <path>
        (r'^cd\s+["\']?([^"\']+)["\']?\s*$', r'Set-Location "\1"'),
        (r'^cd\s*$', 'Set-Location ~'),
        
        # cat <file> → Get-Content <file>
        (r'^cat\s+["\']?([^"\']+)["\']?\s*$', r'Get-Content "\1"'),
        
        # touch <file> → New-Item -ItemType File -Path <file> -Force
        (r'^touch\s+["\']?([^"\']+)["\']?\s*$', r'New-Item -ItemType File -Path "\1" -Force'),
        
        # echo <text> > <file> → Set-Content -Path <file> -Value "<text>"
        # This is complex, skip for now
        
        # find <dir> -name <pattern> → Get-ChildItem -Path <dir> -Filter <pattern> -Recurse
        (r'^find\s+["\']?([^"\']+)["\']?\s+-name\s+["\']?([^"\']+)["\']?\s*$', 
         r'Get-ChildItem -Path "\1" -Filter "\2" -Recurse'),
        
        # which <cmd> → Get-Command <cmd>
        (r'^which\s+["\']?([^"\']+)["\']?\s*$', r'Get-Command "\1"'),
        
        # env | grep <pattern> → Get-ChildItem Env: | Where-Object { $_.Name -match "<pattern>" }
        (r'^env\s*\|\s*grep\s+["\']?([^"\']+)["\']?\s*$', 
         r'Get-ChildItem Env: | Where-Object { $_.Name -match "\1" }'),
        
        # df -h → Get-PSDrive | Select-Object Name, Used, Free
        (r'^df\s+-?h?\s*$', 'Get-PSDrive | Select-Object Name, Used, Free'),
        
        # du -sh <path> → (Get-ChildItem -Path <path> -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
        # Complex, skip
        
        # chmod <mode> <file> → (no direct equivalent, just inform)
        # skip
        
        # ps aux → Get-Process
        (r'^ps\s+aux\s*$', 'Get-Process'),
        (r'^ps\s*$', 'Get-Process'),
        
        # kill <pid> → Stop-Process -Id <pid> -Force
        (r'^kill\s+["\']?(\d+)["\']?\s*$', r'Stop-Process -Id \1 -Force'),
        
        # wget <url> → Invoke-WebRequest -Uri <url> -OutFile <filename>
        # Complex, skip for now
        
        # curl <url> → Invoke-WebRequest -Uri <url>
        (r'^curl\s+["\']?([^"\']+)["\']?\s*$', r'Invoke-WebRequest -Uri "\1"'),
        
        # uname -a → $PSVersionTable
        (r'^uname\s+-?a?\s*$', '$PSVersionTable'),
        
        # whoami → $env:USERNAME
        (r'^whoami\s*$', '$env:USERNAME'),
        
        # date → Get-Date
        (r'^date\s*$', 'Get-Date'),
        
        # clear / cls → Clear-Host
        (r'^clear\s*$', 'Clear-Host'),
        (r'^cls\s*$', 'Clear-Host'),
        
        # grep <pattern> → Select-String -Pattern <pattern>
        (r'^grep\s+["\']?([^"\']+)["\']?\s*$', r'Select-String -Pattern "\1"'),
        
        # wc -l → (Measure-Object -Line).Lines
        (r'^wc\s+-?l?\s*$', '(Measure-Object -Line).Lines'),
        
        # head -n <num> → Select-Object -First <num>
        (r'^head\s+-?n?\s*(\d+)?\s*$', lambda m: f'Select-Object -First {m.group(1) or "10"}'),
        
        # tail -n <num> → Select-Object -Last <num>
        (r'^tail\s+-?n?\s*(\d+)?\s*$', lambda m: f'Select-Object -Last {m.group(1) or "10"}'),
        
        # sort → Sort-Object
        (r'^sort\s*$', 'Sort-Object'),
        
        # uniq → Sort-Object -Unique
        (r'^uniq\s*$', 'Sort-Object -Unique'),
        
        # xargs → ForEach-Object
        # Complex, skip
        
        # awk → ForEach-Object { $_ -split " " | Select-Object <fields> }
        # Complex, skip
        
        # sed → ForEach-Object { $_ -replace "pattern", "replacement" }
        # Complex, skip
        
        # diff → Compare-Object
        (r'^diff\s+["\']?([^"\']+)["\']?\s+["\']?([^"\']+)["\']?\s*$', 
         r'Compare-Object (Get-Content "\1") (Get-Content "\2")'),
        
        # tar -xzf → Expand-Archive
        (r'^tar\s+-?xzf?\s+["\']?([^"\']+)["\']?\s*$', r'Expand-Archive -Path "\1" -DestinationPath "."'),
        
        # zip → Compress-Archive
        (r'^zip\s+-?r?\s+["\']?([^"\']+)["\']?\s+["\']?([^"\']+)["\']?\s*$', 
         r'Compress-Archive -Path "\2" -DestinationPath "\1"'),
        
        # unzip → Expand-Archive
        (r'^unzip\s+["\']?([^"\']+)["\']?\s*$', r'Expand-Archive -Path "\1" -DestinationPath "."'),
    ]
    
    # Handle chained commands with && (convert to PowerShell semicolon)
    if '&&' in cmd:
        parts = cmd.split('&&')
        translated_parts = [_translate_command_to_powershell(p.strip()) for p in parts]
        return '; '.join(translated_parts)
    
    # Handle pipes - translate each part separately
    if '|' in cmd and '||' not in cmd:
        parts = cmd.split('|')
        translated_parts = [_translate_command_to_powershell(p.strip()) for p in parts]
        return ' | '.join(translated_parts)
    
    # Try each translation pattern
    for pattern, replacement in translations:
        match = re.match(pattern, cmd, re.IGNORECASE)
        if match:
            result = re.sub(pattern, replacement, cmd, count=1, flags=re.IGNORECASE)
            return result
    
    # Handle output redirection > - keep as-is (PowerShell supports >)
    
    return cmd


def _translate_command_to_bash(command: str) -> str:
    """
    Translate common PowerShell commands to bash equivalents.
    This helps when LLM models generate PowerShell-style commands on Linux/Mac.
    
    Args:
        command: The original command string
        
    Returns:
        Translated command for bash, or original if no translation needed
    """
    cmd = command.strip()
    
    # PowerShell to bash translations
    translations = [
        # Get-ChildItem → ls
        (r'^Get-ChildItem\s+-Path\s+["\']?([^"\']+)["\']?\s*$', r'ls "\1"'),
        (r'^Get-ChildItem\s*$', 'ls'),
        
        # Get-Content → cat
        (r'^Get-Content\s+["\']?([^"\']+)["\']?\s*$', r'cat "\1"'),
        
        # Set-Location → cd
        (r'^Set-Location\s+["\']?([^"\']+)["\']?\s*$', r'cd "\1"'),
        (r'^Set-Location\s*$', 'cd ~'),
        
        # Get-Location → pwd
        (r'^Get-Location\s*$', 'pwd'),
        
        # New-Item -ItemType Directory → mkdir
        (r'^New-Item\s+-ItemType\s+Directory\s+-Force\s+-Path\s+["\']?([^"\']+)["\']?\s*$', r'mkdir -p "\1"'),
        
        # New-Item -ItemType File → touch
        (r'^New-Item\s+-ItemType\s+File\s+-Path\s+["\']?([^"\']+)["\']?\s*-Force\s*$', r'touch "\1"'),
        
        # Remove-Item → rm
        (r'^Remove-Item\s+-Recurse\s+-Force\s+-Path\s+["\']?([^"\']+)["\']?\s*$', r'rm -rf "\1"'),
        (r'^Remove-Item\s+-Force\s+-Path\s+["\']?([^"\']+)["\']?\s*$', r'rm -f "\1"'),
        
        # Copy-Item → cp
        (r'^Copy-Item\s+-Recurse\s+-Path\s+["\']?([^"\']+)["\']?\s+-Destination\s+["\']?([^"\']+)["\']?\s*$', 
         r'cp -r "\1" "\2"'),
        (r'^Copy-Item\s+-Path\s+["\']?([^"\']+)["\']?\s+-Destination\s+["\']?([^"\']+)["\']?\s*$', 
         r'cp "\1" "\2"'),
        
        # Move-Item → mv
        (r'^Move-Item\s+-Path\s+["\']?([^"\']+)["\']?\s+-Destination\s+["\']?([^"\']+)["\']?\s*$', 
         r'mv "\1" "\2"'),
        
        # Get-Command → which
        (r'^Get-Command\s+["\']?([^"\']+)["\']?\s*$', r'which "\1"'),
        
        # Get-Process → ps
        (r'^Get-Process\s*$', 'ps aux'),
        
        # Stop-Process → kill
        (r'^Stop-Process\s+-Id\s+(\d+)\s*-Force\s*$', r'kill -9 \1'),
        
        # Invoke-WebRequest → curl
        (r'^Invoke-WebRequest\s+-Uri\s+["\']?([^"\']+)["\']?\s*$', r'curl "\1"'),
        
        # Clear-Host → clear
        (r'^Clear-Host\s*$', 'clear'),
        
        # $env:USERNAME → whoami
        (r'^\$env:USERNAME\s*$', 'whoami'),
        
        # $PSVersionTable → uname -a
        (r'^\$PSVersionTable\s*$', 'uname -a'),
        
        # Get-Date → date
        (r'^Get-Date\s*$', 'date'),
        
        # Select-String -Pattern <pattern> → grep <pattern>
        (r'^Select-String\s+-Pattern\s+["\']?([^"\']+)["\']?\s*$', r'grep "\1"'),
        
        # (Measure-Object -Line).Lines → wc -l
        (r'^\(Measure-Object\s+-Line\)\.Lines\s*$', 'wc -l'),
        
        # Select-Object -First <num> → head -n <num>
        (r'^Select-Object\s+-First\s+(\d+)\s*$', r'head -n \1'),
        
        # Select-Object -Last <num> → tail -n <num>
        (r'^Select-Object\s+-Last\s+(\d+)\s*$', r'tail -n \1'),
        
        # Sort-Object → sort
        (r'^Sort-Object\s*$', 'sort'),
        
        # Sort-Object -Unique → sort | uniq
        (r'^Sort-Object\s+-Unique\s*$', 'sort | uniq'),
        
        # Compare-Object → diff
        # Complex, skip
        
        # Expand-Archive → unzip
        (r'^Expand-Archive\s+-Path\s+["\']?([^"\']+)["\']?\s*-DestinationPath\s+["\']?([^"\']+)["\']?\s*$', 
         r'unzip "\1" -d "\2"'),
        
        # Compress-Archive → zip
        (r'^Compress-Archive\s+-Path\s+["\']?([^"\']+)["\']?\s*-DestinationPath\s+["\']?([^"\']+)["\']?\s*$', 
         r'zip "\2" "\1"'),
    ]
    
    # Try each translation pattern
    for pattern, replacement in translations:
        match = re.match(pattern, cmd, re.IGNORECASE)
        if match:
            result = re.sub(pattern, replacement, cmd, count=1, flags=re.IGNORECASE)
            return result
    
    return cmd


def _auto_translate_command(command: str, target_shell: str) -> str:
    """
    Automatically translate a command to the target shell's syntax.
    
    Args:
        command: The original command
        target_shell: 'powershell' or 'bash'
        
    Returns:
        Translated command appropriate for the target shell
    """
    os_name = _detect_host_os()
    
    if target_shell == 'powershell' and os_name != 'windows':
        # On non-Windows, translating to PowerShell doesn't make sense
        return command
    elif target_shell == 'bash' and os_name == 'windows':
        # On Windows, translating to bash doesn't make sense
        return command
    
    # Check if command looks like it's for a different shell
    unix_indicators = ['mkdir -p', 'rm -rf', 'cp -r', 'ls ', 'cat ', 'chmod ', 'chown ']
    powershell_indicators = ['Get-ChildItem', 'Get-Content', 'Set-Location', 'New-Item', 'Remove-Item']
    
    has_unix_syntax = any(ind in command for ind in unix_indicators)
    has_powershell_syntax = any(ind in command for ind in powershell_indicators)
    
    if target_shell == 'powershell' and has_unix_syntax and not has_powershell_syntax:
        # Command looks like Unix but we're targeting PowerShell
        return _translate_command_to_powershell(command)
    elif target_shell == 'bash' and has_powershell_syntax and not has_unix_syntax:
        # Command looks like PowerShell but we're targeting bash
        return _translate_command_to_bash(command)
    
    return command


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


def _get_shell_command(shell: str, command: str, cwd: str = None) -> list:
    """
    Build the subprocess command list for the given shell.
    Returns a list suitable for subprocess.run().
    
    Auto-translates commands if they appear to be for a different shell.
    """
    os_name = _detect_host_os()
    
    # Auto-translate command to target shell syntax
    translated_command = _auto_translate_command(command, shell)
    
    if shell == 'powershell':
        # PowerShell: use -Command for inline commands
        if cwd:
            # Prepend cd to command
            ps_cmd = ['powershell', '-NoProfile', '-Command', f"Set-Location '{cwd}'; {translated_command}"]
        else:
            ps_cmd = ['powershell', '-NoProfile', '-Command', translated_command]
        return ps_cmd
    
    elif shell == 'cmd':
        # CMD: use /c
        cmd_list = ['cmd', '/c', translated_command]
        return cmd_list
    
    else:
        # bash (Linux/macOS)
        if cwd:
            return ['bash', '-c', f"cd '{cwd}' && {translated_command}"]
        return ['bash', '-c', translated_command]


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
    - Auto-translates commands between OS syntaxes (Unix ↔ PowerShell)
    - Auto-detects Python code and writes to temp files (avoids escaping issues)
    
    The agent can use either Unix or PowerShell commands - they will be
    automatically translated to the correct shell syntax for the current OS.
    
    The agent should:
    - Use native OS commands (dir on Windows, ls on Linux) OR
    - Use any OS commands - they will be auto-translated
    - For complex Python, this tool auto-detects and handles it
    - Or use code_execute tool explicitly for Python
    """
    name = "execute_command"
    description = (
        "Execute shell commands on the host OS. "
        "Auto-detects OS and translates commands between Unix/PowerShell syntax. "
        "Python code is auto-detected and handled via temp files. "
        "For explicit Python execution, use code_execute tool."
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
            error_msg = "Failed to execute command. Check permissions and command syntax."
            
            # Provide helpful suggestions based on common errors
            error_str = str(e).lower()
            if 'no such file' in error_str or 'not found' in error_str:
                if os_name == 'windows':
                    error_msg += "\n\nTip: On Windows, use PowerShell commands like:"
                    error_msg += "\n  - New-Item -ItemType Directory -Force -Path 'path' (instead of mkdir -p)"
                    error_msg += "\n  - Get-ChildItem (instead of ls)"
                    error_msg += "\n  - Get-Content (instead of cat)"
                else:
                    error_msg += "\n\nTip: On Linux/Mac, use bash commands like:"
                    error_msg += "\n  - mkdir -p (instead of New-Item)"
                    error_msg += "\n  - ls (instead of Get-ChildItem)"
                    error_msg += "\n  - cat (instead of Get-Content)"
            
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
