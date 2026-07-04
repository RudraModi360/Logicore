"""
logicore Validation Chatbot
============================
Terminal-based interactive bot (no TUI) for testing all logicore features.
"""
import asyncio
import sys
import os
import json
import tempfile
import shutil
import importlib.util
import subprocess
import platform
from datetime import datetime
from typing import Dict, Any, Callable, Optional, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore import Agent, SmartAgent, BasicAgent, CopilotAgent, MCPAgent
from logicore.agent import AgentSession
from logicore.tools.registry import registry
from logicore.tools.base import BaseTool, ToolResult
from logicore.tools import (
    ALL_TOOL_SCHEMAS, SAFE_TOOLS, DANGEROUS_TOOLS,
    execute_tool
)
from logicore.document import get_handler, BaseDocumentHandler
from logicore.session import SessionManager
from logicore.telemetry import TelemetryTracker
from logicore.context_engine.token_estimator import estimate_tokens
from logicore.runtime.context.token_budget import TokenBudget
from logicore.gateway import ProviderGateway
from logicore.skills import Skill, SkillLoader


BANNER = """
╔══════════════════════════════════════════════════════╗
║         logicore Validation Chatbot v2.0             ║
║   Testing restructured module layout & full API       ║
╚══════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
Commands (type in chat or use /):
  /agent [type]        — Switch agent (smart, basic, copilot, mcp, base)
  /tools               — List all loaded tools
  /tool <name>         — Test a tool (e.g., /tool datetime)
  /skills              — Show loaded skills
  /session             — Show session info
  /config              — Show agent config
  /telemetry           — Show telemetry stats
  /token               — Show token budget estimation
  /new                 — Start a new session
  /mode <solo|project> — Switch agent mode (SmartAgent only)
  /raw <text>          — Send raw text bypassing enrichment

  --- MCP Integration ---
  /mcp load <path>     — Load MCP servers from mcp.json
  /mcp list            — List connected MCP servers
  /mcp tools           — List all MCP tools
  /mcp clear           — Disconnect all MCP servers

  --- Custom Tools ---
  /addtool <python_expr> — Add a Python function as a tool
                          Example: /addtool def add(a: int, b: int) -> int: return a + b
  /addtoolfile <path>    — Load tools from a .py file (exports functions)
  /tools custom         — List registered custom tools

  --- Sandbox ---
  /sandbox init           — Create sandbox + activate sandbox mode
  /sandbox enter          — Activate sandbox mode (create if needed)
  /sandbox exit           — Deactivate sandbox mode
  /sandbox run <file>     — Execute a script in the sandbox (py, js, sh, etc.)
  /sandbox exec <code>    — Run inline code in the sandbox
  /sandbox ls             — List files in the sandbox
  /sandbox cat <file>     — Read a file from the sandbox
  /sandbox write <file>   — Write content to a sandbox file (interactive)
  /sandbox trust <path>   — Trust a local path (skip permission prompts)
  /sandbox untrust [path] — Remove trust / list trusted paths
  /sandbox denyall        — Deny all local file access (no prompts)
  /sandbox allowall       — Restore prompting for local access
  /sandbox clean          — Delete the sandbox

  /help                — Show this help
  /quit                — Exit

Agent Types:
  smart   — SmartAgent: General reasoning with web search, notes, datetime, bash, cron
  basic   — BasicAgent: Generic customizable agent (minimal tools)
  copilot — CopilotAgent: Coding-focused with filesystem & execution tools
  mcp     — MCPAgent: MCP-enhanced with deferred tool loading
  base    — Agent: Base agent with full tool support
"""


class Sandbox:
    """Manages a temporary sandbox environment in the OS temp directory."""

    SANDBOX_PREFIX = "logicore_sandbox_"

    def __init__(self):
        self.sandbox_dir: Optional[str] = None

    def init(self) -> str:
        """Create a fresh sandbox directory."""
        if self.sandbox_dir and os.path.exists(self.sandbox_dir):
            return f"Sandbox already exists: {self.sandbox_dir}"
        tmp = tempfile.mkdtemp(prefix=self.SANDBOX_PREFIX)
        self.sandbox_dir = tmp
        return f"Sandbox created: {tmp}"

    def _ensure(self) -> str:
        if not self.sandbox_dir or not os.path.exists(self.sandbox_dir):
            return self.init()
        return self.sandbox_dir

    def _resolve(self, name: str) -> str:
        self._ensure()
        safe = os.path.normpath(os.path.join(self.sandbox_dir, name))
        if not safe.startswith(self.sandbox_dir):
            raise ValueError("Path traversal not allowed")
        return safe

    def list_files(self) -> str:
        path = self._ensure()
        entries = []
        for entry in os.scandir(path):
            kind = "d" if entry.is_dir() else "f"
            size = entry.stat().st_size if entry.is_file() else 0
            entries.append(f"  {'[DIR]' if kind == 'd' else '[FIL]:<6'} {entry.name}  ({size} bytes)")
        return "\n".join(entries) if entries else "(empty)"

    def cat_file(self, name: str) -> str:
        fp = self._resolve(name)
        if not os.path.isfile(fp):
            return f"File not found: {name}"
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def write_file(self, name: str, content: str) -> str:
        fp = self._resolve(name)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {fp}"

    def run_file(self, name: str) -> str:
        fp = self._resolve(name)
        if not os.path.isfile(fp):
            return f"File not found: {name}"
        ext = os.path.splitext(fp)[1].lower()
        cmd_map = {
            ".py": [sys.executable, fp],
            ".js": ["node", fp],
            ".sh": ["bash", fp],
            ".ps1": ["powershell", "-ExecutionPolicy", "Bypass", "-File", fp],
            ".bat": ["cmd", "/c", fp],
            ".rb": ["ruby", fp],
            ".pl": ["perl", fp],
            ".go": None,
            ".rs": None,
        }
        if ext in (".go", ".rs"):
            return f"Compiled language '{ext}' requires build step. Compile manually then run the binary."
        cmd = cmd_map.get(ext)
        if not cmd:
            return f"Unsupported file type: {ext}. Supported: {', '.join(cmd_map.keys())}"
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=self.sandbox_dir
            )
            out = result.stdout
            err = result.stderr
            status = "SUCCESS" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
            parts = [f"--- Sandbox Run: {name} ---", f"Status: {status}"]
            if out.strip():
                parts.append(f"Stdout:\n{out.strip()}")
            if err.strip():
                parts.append(f"Stderr:\n{err.strip()}")
            return "\n".join(parts)
        except FileNotFoundError:
            return f"Runtime not found for {ext}. Is it installed?"
        except subprocess.TimeoutExpired:
            return "Execution timed out (30s limit)."

    def exec_code(self, code: str, lang: str = "python") -> str:
        ext_map = {
            "python": ".py", "py": ".py",
            "javascript": ".js", "js": ".js",
            "bash": ".sh", "sh": ".sh",
            "ruby": ".rb", "rb": ".rb",
            "perl": ".pl", "pl": ".pl",
        }
        ext = ext_map.get(lang.lower(), ".py")
        fname = f"_sandbox_exec_{int(datetime.now().timestamp())}{ext}"
        self.write_file(fname, code)
        return self.run_file(fname)

    def cleanup(self) -> str:
        if self.sandbox_dir and os.path.exists(self.sandbox_dir):
            path = self.sandbox_dir
            shutil.rmtree(path, ignore_errors=True)
            self.sandbox_dir = None
            return f"Sandbox deleted: {path}"
        self.sandbox_dir = None
        return "No sandbox to clean."

    def status(self) -> str:
        if self.sandbox_dir and os.path.exists(self.sandbox_dir):
            return f"Sandbox active: {self.sandbox_dir}"
        return "No active sandbox. Use /sandbox init to create one."


class Chatbot:
    # Available agent types with descriptions
    AGENT_TYPES = {
        "smart": {"name": "SmartAgent", "class": "SmartAgent", "desc": "General reasoning with web search, notes, datetime, bash, cron"},
        "basic": {"name": "BasicAgent", "class": "BasicAgent", "desc": "Generic customizable agent (minimal tools)"},
        "copilot": {"name": "CopilotAgent", "class": "CopilotAgent", "desc": "Coding-focused with filesystem & execution tools"},
        "mcp": {"name": "MCPAgent", "class": "MCPAgent", "desc": "MCP-enhanced with deferred tool loading"},
        "base": {"name": "Agent", "class": "Agent", "desc": "Base agent with full tool support"},
    }

    SANDBOX_CONTEXT_TEMPLATE = (
        "[SANDBOX MODE ACTIVE]\n"
        "You are operating inside an isolated sandbox environment.\n"
        "All file operations and commands execute in: {sandbox_dir}\n"
        "Rules:\n"
        "- Use working_directory=\"{sandbox_dir}\" when calling execute_command\n"
        "- Use file paths relative to this sandbox directory\n"
        "- List files with directory=\"{sandbox_dir}\"\n"
        "- Read/Write files using paths relative to this directory\n"
        "- Do NOT access files outside this sandbox\n"
        "- If you need to access local files OUTSIDE the sandbox, you MUST ask the user first\n"
        "Sandbox is cleaned automatically on chatbot exit.\n\n"
    )

    # Tools that access the filesystem or execute system commands
    SANDBOX_SENSITIVE_TOOLS = {
        "read_file", "write_file", "edit_file", "create_file", "delete_file",
        "execute_command", "code_execute", "git_command",
        "read_document", "convert_document",
        "edit_pptx", "create_pptx", "append_slide",
        "edit_docx", "create_docx",
        "edit_excel", "create_excel",
        "merge_pdfs", "split_pdf",
    }

    def __init__(self, provider: str = "ollama", model: str = None):
        self.provider_name = provider
        self.model_name = model or "gpt-oss:20b-cloud"
        self.current_agent_type = "smart"
        self.custom_tools: List[Dict[str, Any]] = []
        self.sandbox = Sandbox()
        self.sandbox_active = False
        self.sandbox_trusted_paths: List[str] = []
        self.sandbox_deny_all_local = False
        self._init_agent()
        self._init_subsystems()

    def _init_agent(self):
        """Initialize the default agent (SmartAgent)."""
        self.agent = self._create_agent("smart")
        self.session_id = "default"
        print(f"[Init] Agent created: {self.current_agent_type} with {self._get_agent_tool_count()} tools")

    def _create_agent(self, agent_type: str):
        """Create an agent of the specified type."""
        agent_type = agent_type.lower()
        if agent_type not in self.AGENT_TYPES:
            raise ValueError(f"Unknown agent type: {agent_type}. Available: {list(self.AGENT_TYPES.keys())}")

        config = self.AGENT_TYPES[agent_type]
        agent_class_name = config["class"]

        common_kwargs = {
            "llm": self.provider_name,
            "model": self.model_name,
            "debug": True,
            "telemetry": True,
            "memory": False,
            "max_iterations": 100,
        }

        if agent_class_name == "SmartAgent":
            agent = SmartAgent(**common_kwargs)
        elif agent_class_name == "BasicAgent":
            agent = BasicAgent(
                name="TestBot",
                description="A test agent for validation",
                provider=self.provider_name,
                model=self.model_name,
                debug=True,
                telemetry=True,
                memory_enabled=False,
                max_iterations=100,
            )
        elif agent_class_name == "CopilotAgent":
            agent = CopilotAgent(
                llm=self.provider_name,
                model=self.model_name,
                debug=True,
                telemetry=True,
                memory=False,
            )
        elif agent_class_name == "MCPAgent":
            agent = MCPAgent(
                provider=self.provider_name,
                model=self.model_name,
                debug=True,
                telemetry=True,
                memory=False,
            )
        elif agent_class_name == "Agent":
            agent = Agent(
                llm=self.provider_name,
                model=self.model_name,
                debug=True,
                telemetry=True,
                memory=False,
                tools=True,
                max_iterations=100,
            )
        else:
            raise ValueError(f"Unknown agent class: {agent_class_name}")

        # Re-register any previously loaded custom tools onto the new agent
        for ct in self.custom_tools:
            self._apply_custom_tool(agent, ct)

        self.current_agent_type = agent_type
        return agent

    def _apply_custom_tool(self, agent, tool_info: Dict[str, Any]):
        """Apply a custom tool definition to an agent instance."""
        schema = tool_info["schema"]
        executor = tool_info["executor"]
        tool_name = schema.get("function", {}).get("name")

        # Determine the actual agent instance (BasicAgent wraps Agent)
        target = getattr(agent, "_agent", agent)
        if hasattr(target, "add_custom_tool"):
            target.add_custom_tool(schema, executor)
        elif hasattr(target, "internal_tools"):
            target.internal_tools.append(schema)
            if hasattr(target, "custom_tool_executors"):
                target.custom_tool_executors[tool_name] = executor
            target.supports_tools = True

    def _get_agent_tool_count(self) -> int:
        if hasattr(self.agent, 'internal_tools'):
            return len(self.agent.internal_tools)
        elif hasattr(self.agent, '_agent') and hasattr(self.agent._agent, 'internal_tools'):
            return len(self.agent._agent.internal_tools)
        elif hasattr(self.agent, 'tools'):
            return len(self.agent.tools)
        return 0

    def _get_agent_skills(self) -> list:
        if hasattr(self.agent, 'skills'):
            return self.agent.skills
        return []

    def _switch_agent(self, agent_type: str):
        agent_type = agent_type.lower()
        if agent_type not in self.AGENT_TYPES:
            return False, f"Unknown agent type: {agent_type}. Available: {list(self.AGENT_TYPES.keys())}"
        if agent_type == self.current_agent_type:
            return False, f"Already using {agent_type} agent"

        old_messages = []
        if hasattr(self, 'agent') and hasattr(self.agent, 'sessions'):
            old_session = self.agent.sessions.get(self.session_id)
            if old_session:
                old_messages = old_session.messages.copy()

        self.agent = self._create_agent(agent_type)

        if old_messages:
            new_session = self.agent.get_session(self.session_id)
            new_session.messages = old_messages

        config = self.AGENT_TYPES[agent_type]
        return True, f"Switched to {config['name']} ({config['desc']})"

    def _init_subsystems(self):
        try:
            self.session_mgr = SessionManager()
            print(f"[Init] SessionManager OK")
        except Exception as e:
            print(f"[Init] SessionManager: {e}")

        try:
            self.telemetry = TelemetryTracker(enabled=True)
            print(f"[Init] TelemetryTracker OK")
        except Exception as e:
            print(f"[Init] TelemetryTracker: {e}")

        try:
            self.token_budget = TokenBudget(model_name=self.model_name)
            print(f"[Init] TokenBudget OK (window: {self.token_budget.context_window})")
        except Exception as e:
            print(f"[Init] TokenBudget: {e}")

        print(f"[Init] Registry has {len(registry._tools)} tools loaded")
        print(f"[Init] SAFE_TOOLS: {len(SAFE_TOOLS)}, DANGEROUS_TOOLS: {len(DANGEROUS_TOOLS)}")

    # --- Sandbox Permission System ---

    def _is_local_path(self, path: str) -> bool:
        """Check if a path points to local machine (outside sandbox)."""
        if not path or not isinstance(path, str):
            return False
        sandbox_dir = self.sandbox.sandbox_dir
        if not sandbox_dir:
            return True
        try:
            abs_path = os.path.abspath(path)
            sandbox_abs = os.path.abspath(sandbox_dir)
            return not abs_path.startswith(sandbox_abs)
        except Exception:
            return True

    def _extract_paths_from_args(self, args: Dict[str, Any]) -> List[str]:
        """Extract all file/directory paths from tool arguments."""
        paths = []
        path_keys = [
            "file_path", "directory", "working_directory",
            "path", "src", "dest", "target", "source",
        ]
        for key in path_keys:
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                paths.append(val.strip())
        return paths

    def _is_tool_local_access(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Check if a tool call tries to access local files outside sandbox."""
        if tool_name not in self.SANDBOX_SENSITIVE_TOOLS:
            return False
        if self.sandbox_deny_all_local:
            return True
        paths = self._extract_paths_from_args(args)
        for p in paths:
            if self._is_local_path(p):
                return True
        return False

    def _is_path_trusted(self, path: str) -> bool:
        """Check if a local path is in the trusted list."""
        try:
            abs_path = os.path.abspath(path)
        except Exception:
            return False
        for trusted in self.sandbox_trusted_paths:
            try:
                if abs_path.startswith(os.path.abspath(trusted)):
                    return True
            except Exception:
                continue
        return False

    async def _sandbox_tool_approval(self, session_id: str, tool_name: str, args: Dict[str, Any]):
        """
        Approval callback for sandbox mode. Intercepts tool calls that
        access local files and asks user for permission.
        """
        if not self.sandbox_active:
            return True

        if not self._is_tool_local_access(tool_name, args):
            return True

        paths = self._extract_paths_from_args(args)
        local_paths = [p for p in paths if self._is_local_path(p)]

        if not local_paths:
            return True

        # Check if any path is already trusted
        all_trusted = all(self._is_path_trusted(p) for p in local_paths)
        if all_trusted:
            return True

        # Build permission prompt
        path_display = "\n".join(f"    {p}" for p in local_paths)
        print(f"\n{'='*60}")
        print(f"[SANDBOX] Agent wants to access LOCAL files:")
        print(path_display)
        print(f"  Tool: {tool_name}")
        print(f"  Args: {json.dumps(args, indent=2, default=str)[:300]}")
        print(f"{'='*60}")
        print(f"  [y] Allow once")
        print(f"  [a] Allow + trust path (for this session)")
        print(f"  [d] Deny")
        print(f"  [q] Deny all local access for this session")

        try:
            choice = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n  Your choice (y/a/d/q): ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            return False

        if choice == "y":
            return True
        elif choice == "a":
            for p in local_paths:
                try:
                    abs_p = os.path.abspath(p)
                    if abs_p not in [os.path.abspath(t) for t in self.sandbox_trusted_paths]:
                        self.sandbox_trusted_paths.append(abs_p)
                        print(f"  [Trusted] {abs_p}")
                except Exception:
                    pass
            return True
        elif choice == "q":
            self.sandbox_deny_all_local = True
            print("  [Session] All local access denied for this session.")
            return False
        else:
            return False

    def _setup_sandbox_callbacks(self):
        """Set up approval callbacks on the agent for sandbox mode."""
        target = getattr(self.agent, "_agent", self.agent)
        if hasattr(target, "set_callbacks"):
            target.set_callbacks(on_tool_approval=self._sandbox_tool_approval)
        elif hasattr(target, "callbacks"):
            target.callbacks["on_tool_approval"] = self._sandbox_tool_approval

    def _clear_sandbox_callbacks(self):
        """Remove sandbox approval callbacks."""
        target = getattr(self.agent, "_agent", self.agent)
        if hasattr(target, "callbacks"):
            target.callbacks["on_tool_approval"] = None

    # --- Tool Listing ---

    def _list_tools(self) -> str:
        lines = ["\n--- Loaded Tools ---"]
        agent_tools = []
        if hasattr(self.agent, 'internal_tools'):
            agent_tools = self.agent.internal_tools
        elif hasattr(self.agent, '_agent') and hasattr(self.agent._agent, 'internal_tools'):
            agent_tools = self.agent._agent.internal_tools

        for tool in agent_tools:
            if isinstance(tool, dict):
                name = tool.get("function", {}).get("name", "unknown")
            else:
                name = str(tool)
            safety = ""
            if name in SAFE_TOOLS:
                safety = " [SAFE]"
            elif name in DANGEROUS_TOOLS:
                safety = " [DANGEROUS]"
            elif name in ['web_search', 'image_search', 'url_fetch', 'convert_document',
                          'edit_pptx', 'create_pptx', 'append_slide', 'edit_docx',
                          'create_docx', 'edit_excel', 'create_excel',
                          'merge_pdfs', 'split_pdf', 'add_cron_job', 'remove_cron_job']:
                safety = " [APPROVAL]"
            lines.append(f"  • {name}{safety}")

        if self.custom_tools:
            lines.append(f"\n--- Custom Tools ({len(self.custom_tools)}) ---")
            for ct in self.custom_tools:
                name = ct["schema"].get("function", {}).get("name", "?")
                lines.append(f"  • {name} [CUSTOM]")

        lines.append(f"\n  Total: {len(agent_tools)} built-in + {len(self.custom_tools)} custom")
        return "\n".join(lines)

    def _show_config(self) -> str:
        agent_config = self.AGENT_TYPES.get(self.current_agent_type, {})
        skills = self._get_agent_skills()
        if self.sandbox_active:
            deny_status = "ALL DENIED" if self.sandbox_deny_all_local else "PROMPT"
            sandbox_status = f"ACTIVE ({self.sandbox.sandbox_dir}) | Local access: {deny_status}"
            if self.sandbox_trusted_paths:
                sandbox_status += f" | Trusted: {len(self.sandbox_trusted_paths)} paths"
        else:
            sandbox_status = "inactive"
        return (
            f"\n--- Agent Config ---\n"
            f"  Agent type: {self.current_agent_type} ({agent_config.get('name', type(self.agent).__name__)})\n"
            f"  Provider: {self.provider_name}\n"
            f"  Model: {self.model_name}\n"
            f"  Session: {self.session_id}\n"
            f"  Tools loaded: {self._get_agent_tool_count()}\n"
            f"  Custom tools: {len(self.custom_tools)}\n"
            f"  Skills loaded: {len(skills)}\n"
            f"  Telemetry: {getattr(self.agent, 'telemetry_enabled', 'N/A')}\n"
            f"  Max iterations: {getattr(self.agent, 'max_iterations', 'N/A')}\n"
            f"  Supports tools: {getattr(self.agent, 'supports_tools', 'N/A')}\n"
            f"  Sandbox mode: {sandbox_status}\n"
            f"  Description: {agent_config.get('desc', 'N/A')}\n"
        )

    def _show_memory_status(self) -> str:
        return "\n--- Memory Status ---\n  Memory system has been removed.\n"

    def _show_telemetry(self) -> str:
        try:
            t = self.agent.telemetry
            return f"\n--- Telemetry ---\n{t}\n" if isinstance(t, dict) else f"\n--- Telemetry ---\n{t}\n"
        except Exception as e:
            return f"\n--- Telemetry ---\nError: {e}\n"

    def _show_token_estimate(self, text: str = None) -> str:
        if text:
            est = estimate_tokens(text)
            return f"Token estimate for '{text[:50]}...': ~{est} tokens"
        budget = self.token_budget.get_status()
        return (
            f"\n--- Token Budget ---\n"
            f"  Model: {budget['model']}\n"
            f"  Context window: {budget['context_window']}\n"
            f"  Used: {budget['used']}\n"
            f"  Remaining: {budget['remaining']}\n"
            f"  Usage: {budget['usage_percent']}%\n"
        )

    async def _execute_tool_test(self, tool_name: str) -> str:
        name_lower = tool_name.lower()
        tool = registry.get_tool(name_lower)
        if not tool:
            available = sorted(registry._tools.keys())
            return f"Tool '{tool_name}' not found. Available: {', '.join(available[:10])}..."

        demo_args = {
            "datetime": {"operation": "now"},
            "read_file": {"file_path": os.path.abspath(__file__)},
            "list_files": {"directory": "."},
            "search_files": {"pattern": "*.py", "directory": "."},
            "fast_grep": {"pattern": "class", "include": "*.py", "directory": "."},
            "web_search": {"query": "Python programming language"},
            "url_fetch": {"url": "https://example.com"},
            "execute_command": {"command": "echo 'hello from logicore'"},
            "git_command": {"command": "status"},
        }

        args = demo_args.get(name_lower, {})
        try:
            result = execute_tool(name_lower, args)
            content_preview = str(result.get("content", ""))[:300]
            return (
                f"\n--- Tool Test: {tool_name} ---\n"
                f"  Args: {args}\n"
                f"  Success: {result.get('success')}\n"
                f"  Result: {content_preview}\n"
            )
        except Exception as e:
            return f"\n--- Tool Test: {tool_name} ---\n  Error: {e}\n"

    # --- Custom Tool Registration ---

    def _add_custom_tool_from_function(self, func: Callable) -> str:
        """Register a callable as a custom tool on the current agent."""
        target = getattr(self.agent, "_agent", self.agent)
        if hasattr(target, "register_tool_from_function"):
            target.register_tool_from_function(func)
        elif hasattr(target, "internal_tools") and hasattr(target, "custom_tool_executors"):
            import inspect
            from typing import get_type_hints
            import re
            name = func.__name__
            raw_doc = func.__doc__ or f"Execute {name}"
            doc_lines = raw_doc.strip().split("\n")
            description_lines = []
            for line in doc_lines:
                s = line.strip()
                if s and not s.lower().startswith(("args:", "returns:", "raises:", "params:")):
                    description_lines.append(s)
            description = " ".join(description_lines) if description_lines else raw_doc.strip()
            sig = inspect.signature(func)
            type_hints = get_type_hints(func)
            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                pt = type_hints.get(pname, str)
                jt = "string"
                if pt == int: jt = "integer"
                elif pt == float: jt = "number"
                elif pt == bool: jt = "boolean"
                elif pt == list: jt = "array"
                elif pt == dict: jt = "object"
                properties[pname] = {"type": jt, "description": f"The {pname.replace('_', ' ')} value"}
                if param.default == inspect.Parameter.empty:
                    required.append(pname)
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": properties, "required": required}
                }
            }
            def executor(**kwargs):
                try:
                    result = func(**kwargs)
                    return ToolResult(success=True, content=str(result))
                except Exception as e:
                    return ToolResult(success=False, error=str(e))
            target.internal_tools.append(schema)
            target.custom_tool_executors[name] = executor
            target.supports_tools = True
            if hasattr(target, "_rebuild_system_prompt_with_tools"):
                target._rebuild_system_prompt_with_tools()
        else:
            return "Agent does not support custom tool registration."

        tool_name = func.__name__
        self.custom_tools.append({
            "schema": {
                "type": "function",
                "function": {"name": tool_name, "description": (func.__doc__ or "")[:200].strip()}
            },
            "executor": None
        })
        return f"Registered custom tool: {tool_name}"

    def _add_custom_tool_from_file(self, filepath: str) -> str:
        """Load all functions from a .py file as custom tools."""
        if not os.path.isfile(filepath):
            return f"File not found: {filepath}"
        spec = importlib.util.spec_from_file_location("_custom_module", filepath)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            return f"Failed to load module: {e}"

        loaded = []
        target = getattr(self.agent, "_agent", self.agent)
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if callable(obj) and not attr_name.startswith("_"):
                if hasattr(target, "register_tool_from_function"):
                    try:
                        target.register_tool_from_function(obj)
                        loaded.append(attr_name)
                    except Exception:
                        pass
                elif hasattr(target, "internal_tools"):
                    import inspect
                    from typing import get_type_hints
                    name = obj.__name__
                    sig = inspect.signature(obj)
                    type_hints = get_type_hints(obj)
                    properties = {}
                    required = []
                    for pname, param in sig.parameters.items():
                        if pname == "self":
                            continue
                        pt = type_hints.get(pname, str)
                        jt = "string"
                        if pt == int: jt = "integer"
                        elif pt == float: jt = "number"
                        elif pt == bool: jt = "boolean"
                        properties[pname] = {"type": jt, "description": f"The {pname.replace('_', ' ')} value"}
                        if param.default == inspect.Parameter.empty:
                            required.append(pname)
                    schema = {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": (obj.__doc__ or f"Execute {name}").strip(),
                            "parameters": {"type": "object", "properties": properties, "required": required}
                        }
                    }
                    target.internal_tools.append(schema)
                    target.custom_tool_executors[name] = obj
                    target.supports_tools = True
                    loaded.append(name)
        if loaded and hasattr(target, "_rebuild_system_prompt_with_tools"):
            target._rebuild_system_prompt_with_tools()
        self.custom_tools.extend([{"schema": {"type": "function", "function": {"name": n}}, "executor": None} for n in loaded])
        return f"Loaded {len(loaded)} tool(s) from {filepath}: {', '.join(loaded)}" if loaded else "No callable functions found in file."

    # --- MCP Management ---

    async def _mcp_load(self, config_path: str) -> str:
        """Load MCP servers from a config file."""
        config_path = config_path.strip().strip('"').strip("'")
        if not os.path.isfile(config_path):
            return f"Config file not found: {config_path}"
        try:
            await self.agent.add_mcp_server(config_path=config_path)
            return f"MCP servers loaded from: {config_path}"
        except Exception as e:
            return f"Failed to load MCP servers: {e}"

    async def _mcp_list(self) -> str:
        """List connected MCP servers."""
        managers = getattr(self.agent, "mcp_managers", [])
        if not managers:
            return "No MCP servers connected."
        lines = ["\n--- MCP Servers ---"]
        for i, mgr in enumerate(managers):
            servers = list(mgr.sessions.keys()) if hasattr(mgr, "sessions") else []
            tools_count = len(mgr.server_tools_map) if hasattr(mgr, "server_tools_map") else 0
            lines.append(f"  [{i}] Servers: {', '.join(servers) or '(none)'}")
            lines.append(f"      Tools mapped: {tools_count}")
        return "\n".join(lines)

    async def _mcp_tools(self) -> str:
        """List all available MCP tools."""
        managers = getattr(self.agent, "mcp_managers", [])
        if not managers:
            return "No MCP servers connected."
        lines = ["\n--- MCP Tools ---"]
        total = 0
        for mgr in managers:
            if hasattr(mgr, "get_tools"):
                tools = await mgr.get_tools()
                for t in tools:
                    name = t.get("function", {}).get("name", "?")
                    desc = t.get("function", {}).get("description", "")[:80]
                    lines.append(f"  • {name}: {desc}")
                    total += 1
        lines.append(f"\n  Total MCP tools: {total}")
        return "\n".join(lines)

    async def _mcp_clear(self) -> str:
        """Disconnect all MCP servers."""
        try:
            await self.agent.clear_mcp_servers()
            return "All MCP servers disconnected."
        except Exception as e:
            return f"Error clearing MCP servers: {e}"

    # --- Main Loop ---

    async def start(self):
        print(BANNER)
        print(HELP_TEXT)

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("\nYou: ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nCleaning up...")
                self.sandbox_active = False
                self.sandbox_trusted_paths = []
                self.sandbox_deny_all_local = False
                self._clear_sandbox_callbacks()
                self.sandbox.cleanup()
                print("Bye!")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                await self._handle_command(user_input)
                continue

            # Inject sandbox context if sandbox mode is active
            effective_input = user_input
            if self.sandbox_active and self.sandbox.sandbox_dir:
                sandbox_dir = self.sandbox.sandbox_dir
                ctx_prefix = self.SANDBOX_CONTEXT_TEMPLATE.format(sandbox_dir=sandbox_dir)
                effective_input = ctx_prefix + user_input

            prompt_tag = " [sandbox]" if self.sandbox_active else ""
            print(f"\nAgent{prompt_tag}: ", end="", flush=True)
            try:
                response = await self.agent.chat(
                    effective_input,
                    session_id=self.session_id,
                    stream=True,
                    streaming_funct=lambda token: print(token, end="", flush=True),
                )
                print()
            except Exception as e:
                print(f"\n[Error] {e}")

    async def _handle_command(self, cmd: str):
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # --- Quit / Help ---
        if command == "/quit":
            self.sandbox_active = False
            self.sandbox_trusted_paths = []
            self.sandbox_deny_all_local = False
            self._clear_sandbox_callbacks()
            self.sandbox.cleanup()
            print("Bye!")
            sys.exit(0)
        elif command == "/help":
            print(HELP_TEXT)

        # --- Tools / Config / Session ---
        elif command == "/tools":
            if arg.strip() == "custom":
                if not self.custom_tools:
                    print("\nNo custom tools registered.")
                else:
                    print("\n--- Custom Tools ---")
                    for ct in self.custom_tools:
                        name = ct["schema"].get("function", {}).get("name", "?")
                        print(f"  • {name}")
            else:
                print(self._list_tools())
        elif command == "/tool":
            if arg:
                result = await self._execute_tool_test(arg)
                print(result)
            else:
                print("Usage: /tool <tool_name>")
        elif command == "/config":
            print(self._show_config())
        elif command == "/session":
            session = self.agent.get_session(self.session_id)
            print(
                f"\n--- Session ---\n"
                f"  ID: {session.session_id}\n"
                f"  Messages: {len(session.messages)}\n"
                f"  Created: {session.created_at}\n"
                f"  Active: {session.last_activity}\n"
            )
        elif command == "/memory":
            print(self._show_memory_status())
        elif command == "/telemetry":
            print(self._show_telemetry())
        elif command == "/token":
            print(self._show_token_estimate())
        elif command == "/new":
            self.session_id = f"session_{datetime.now().strftime('%H%M%S')}"
            print(f"Started new session: {self.session_id}")
        elif command == "/mode":
            if hasattr(self.agent, 'set_mode'):
                if arg in ("solo", "project"):
                    self.agent.set_mode(arg)
                    print(f"Switched to {arg} mode")
                else:
                    print("Usage: /mode <solo|project>")
            else:
                print(f"Mode switching not available for {type(self.agent).__name__}")

        # --- Skills ---
        elif command == "/skills":
            skills = self._get_agent_skills()
            if skills:
                print(f"\nLoaded skills: {[s.name for s in skills]}")
            else:
                print("\nNo skills loaded")

        # --- Agent ---
        elif command == "/agent":
            if arg:
                success, msg = self._switch_agent(arg)
                if success:
                    print(f"\n{msg}")
                    print(f"  Tools loaded: {self._get_agent_tool_count()}")
                    print(f"  Session preserved: {self.session_id}")
                else:
                    print(f"\n{msg}")
            else:
                print("\n--- Available Agents ---")
                for atype, config in self.AGENT_TYPES.items():
                    current = " (ACTIVE)" if atype == self.current_agent_type else ""
                    print(f"  • {atype}{current} -- {config['name']}: {config['desc']}")
                print(f"\nUsage: /agent <type>")
                print(f"Types: {', '.join(self.AGENT_TYPES.keys())}")

        # --- MCP Integration ---
        elif command == "/mcp":
            sub = arg.strip().lower()
            if sub.startswith("load"):
                path_arg = arg.strip()[4:].strip() if len(arg.strip()) > 4 else ""
                if path_arg:
                    result = await self._mcp_load(path_arg)
                    print(result)
                else:
                    print("Usage: /mcp load <path_to_mcp.json>")
            elif sub == "list":
                result = await self._mcp_list()
                print(result)
            elif sub == "tools":
                result = await self._mcp_tools()
                print(result)
            elif sub == "clear":
                result = await self._mcp_clear()
                print(result)
            else:
                print("MCP Commands:")
                print("  /mcp load <path>  -- Load MCP servers from mcp.json")
                print("  /mcp list         -- List connected MCP servers")
                print("  /mcp tools        -- List all MCP tools")
                print("  /mcp clear        -- Disconnect all MCP servers")

        # --- Custom Tool Registration ---
        elif command == "/addtool":
            if not arg:
                print("Usage: /addtool <python_function>")
                print("Example: /addtool def add(a: int, b: int) -> int: return a + b")
                return
            try:
                func = self._parse_inline_function(arg)
                result = self._add_custom_tool_from_function(func)
                print(result)
            except Exception as e:
                print(f"Failed to add tool: {e}")
        elif command == "/addtoolfile":
            if not arg:
                print("Usage: /addtoolfile <path_to_python_file>")
                return
            result = self._add_custom_tool_from_file(arg.strip())
            print(result)

        # --- Sandbox ---
        elif command == "/sandbox":
            await self._handle_sandbox(arg)
        else:
            print(f"Unknown command: {command}. Type /help for available commands.")

    # --- Sandbox Handler ---

    async def _handle_sandbox(self, arg: str):
        sub = arg.strip()
        tokens = sub.split(maxsplit=1)
        subcmd = tokens[0].lower() if tokens else ""
        subarg = tokens[1] if len(tokens) > 1 else ""

        if subcmd == "init":
            result = self.sandbox.init()
            self.sandbox_active = True
            self.sandbox_trusted_paths = []
            self.sandbox_deny_all_local = False
            self._setup_sandbox_callbacks()
            print(result)
            print(f"Sandbox mode ACTIVATED. All prompts now run in: {self.sandbox.sandbox_dir}")
            print("Agent will ask permission before accessing local files outside sandbox.")
            print("Use /sandbox exit to deactivate, /sandbox clean to delete.")
        elif subcmd == "enter":
            if not self.sandbox.sandbox_dir or not os.path.exists(self.sandbox.sandbox_dir):
                result = self.sandbox.init()
                print(result)
            self.sandbox_active = True
            self.sandbox_trusted_paths = []
            self.sandbox_deny_all_local = False
            self._setup_sandbox_callbacks()
            print(f"Sandbox mode ACTIVATED. Agent now operates in: {self.sandbox.sandbox_dir}")
            print("Agent will ask permission before accessing local files outside sandbox.")
        elif subcmd == "exit":
            self.sandbox_active = False
            self.sandbox_trusted_paths = []
            self.sandbox_deny_all_local = False
            self._clear_sandbox_callbacks()
            print("Sandbox mode DEACTIVATED. Agent now runs normally.")
        elif subcmd == "ls":
            print(self.sandbox.list_files())
        elif subcmd == "cat":
            if subarg:
                print(self.sandbox.cat_file(subarg.strip()))
            else:
                print("Usage: /sandbox cat <filename>")
        elif subcmd == "run":
            if subarg:
                print(self.sandbox.run_file(subarg.strip()))
            else:
                print("Usage: /sandbox run <filename>")
        elif subcmd == "exec":
            if not subarg:
                print("Usage: /sandbox exec <inline_code>")
                return
            lang = "python"
            code = subarg
            if " " in subarg:
                first_word = subarg.split()[0].lower()
                if first_word in ("py", "python", "js", "javascript", "sh", "bash", "rb", "ruby", "pl", "perl"):
                    lang = first_word
                    code = subarg.split(None, 1)[1]
            print(self.sandbox.exec_code(code, lang))
        elif subcmd == "write":
            if not subarg:
                print("Usage: /sandbox write <filename>")
                return
            fname = subarg.strip()
            print(f"Enter content for {fname} (end with EOF on its own line):")
            lines = []
            try:
                while True:
                    line = await asyncio.get_event_loop().run_in_executor(None, lambda: input())
                    if line.strip() == "EOF":
                        break
                    lines.append(line)
            except (EOFError, KeyboardInterrupt):
                pass
            content = "\n".join(lines)
            print(self.sandbox.write_file(fname, content))
        elif subcmd == "clean":
            self.sandbox_active = False
            self.sandbox_trusted_paths = []
            self.sandbox_deny_all_local = False
            self._clear_sandbox_callbacks()
            print(self.sandbox.cleanup())
        elif subcmd == "trust":
            if not subarg:
                print("Usage: /sandbox trust <path>  -- Trust a local path for this session")
                return
            trust_path = subarg.strip()
            try:
                abs_p = os.path.abspath(trust_path)
                if abs_p not in self.sandbox_trusted_paths:
                    self.sandbox_trusted_paths.append(abs_p)
                print(f"Trusted: {abs_p}")
            except Exception as e:
                print(f"Error: {e}")
        elif subcmd == "untrust":
            if not subarg:
                print("Trusted paths:")
                for p in self.sandbox_trusted_paths:
                    print(f"  {p}")
                if not self.sandbox_trusted_paths:
                    print("  (none)")
                return
            untrust_path = subarg.strip()
            try:
                abs_p = os.path.abspath(untrust_path)
                self.sandbox_trusted_paths = [p for p in self.sandbox_trusted_paths if p != abs_p]
                print(f"Removed trust for: {abs_p}")
            except Exception as e:
                print(f"Error: {e}")
        elif subcmd == "denyall":
            self.sandbox_deny_all_local = True
            print("All local file access DENIED for this session.")
        elif subcmd == "allowall":
            self.sandbox_deny_all_local = False
            print("Local file access will now prompt for permission.")
        else:
            status = self.sandbox.status()
            mode = "ACTIVE" if self.sandbox_active else "INACTIVE"
            deny_status = "ALL DENIED" if self.sandbox_deny_all_local else "PROMPT"
            print(f"{status}  |  Sandbox mode: {mode}  |  Local access: {deny_status}")
            if self.sandbox_trusted_paths:
                print(f"  Trusted paths: {len(self.sandbox_trusted_paths)}")
            print("\nSandbox Commands:")
            print("  /sandbox init           -- Create fresh sandbox + activate mode")
            print("  /sandbox enter          -- Activate sandbox mode (create if needed)")
            print("  /sandbox exit           -- Deactivate sandbox mode")
            print("  /sandbox ls             -- List sandbox files")
            print("  /sandbox cat <file>     -- Read a sandbox file")
            print("  /sandbox run <file>     -- Execute a script in sandbox")
            print("  /sandbox exec <code>    -- Run inline code")
            print("  /sandbox write <f>      -- Write content to a file")
            print("  /sandbox trust <path>   -- Trust a local path (allow without prompting)")
            print("  /sandbox untrust [path] -- Remove trust from a path (or list trusted)")
            print("  /sandbox denyall        -- Deny all local access (no prompts)")
            print("  /sandbox allowall       -- Restore prompting for local access")
            print("  /sandbox clean          -- Delete the sandbox")

    # --- Inline Function Parser ---

    def _parse_inline_function(self, text: str) -> Callable:
        """Parse a one-line function definition and return a callable."""
        text = text.strip()
        if text.startswith("def ") or text.startswith("async def "):
            namespace = {}
            try:
                exec(f"async def _fn(): pass", namespace)
                async_fn = namespace["_fn"]
            except Exception:
                namespace = {}
            full = f"{text}\n" if not text.endswith("\n") else text
            try:
                exec(compile(full, "<addtool>", "exec"), namespace)
            except SyntaxError:
                full = f"def {text.split('(', 1)[0].replace('def ', '')}(): pass" if "(" not in text else text
                namespace = {}
                exec(text, namespace)
        else:
            # Treat as expression, wrap in lambda
            namespace = {}
            exec(f"_fn = lambda: {text}", namespace)
        candidates = [v for k, v in namespace.items() if callable(v) and not k.startswith("_")]
        if not candidates:
            raise ValueError("No callable found in expression.")
        return candidates[0]


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    model = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Starting with provider={provider}, model={model or 'default'}")
    bot = Chatbot(provider=provider, model=model)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
