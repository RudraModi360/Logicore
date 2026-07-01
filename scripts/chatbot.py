"""
logicore Validation Chatbot
============================
Terminal-based interactive bot (no TUI) for testing all logicore features.
"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logicore import Agent, SmartAgent, BasicAgent
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
from logicore.context import ContextMiddleware, TokenBudget, estimate_tokens
from logicore.gateway import ProviderGateway
from logicore.skills import Skill, SkillLoader


BANNER = """
╔══════════════════════════════════════════════════════╗
║         logicore Validation Chatbot v1.0             ║
║   Testing restructured module layout & full API       ║
╚══════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
Commands (type in chat or use /):
  /tools          — List all loaded tools
  /tool <name>    — Test a tool (e.g., /tool datetime)
  /skills         — Show loaded skills
  /session        — Show session info
  /config         — Show agent config
  /telemetry      — Show telemetry stats
  /token          — Show token budget estimation
  /new            — Start a new session
  /mode <solo|project> — Switch agent mode
  /raw <text>     — Send raw text bypassing enrichment
  /help           — Show this help
  /quit           — Exit
"""


class Chatbot:
    def __init__(self, provider: str = "ollama", model: str = None):
        self.provider_name = provider
        self.model_name = model or "gpt-oss:20b-cloud"
        self._init_agent()
        self._init_subsystems()

    def _init_agent(self):
        self.agent = SmartAgent(
            llm=self.provider_name,
            model=self.model_name,
            debug=True,
            telemetry=True,
            memory=False,  # Memory removed
            # tools=True,
            max_iterations=100,
        )
        self.session_id = "default"
        print(f"[Init] Agent created with {len(self.agent.internal_tools)} tools")

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

        # Memory system has been removed

        print(f"[Init] Registry has {len(registry._tools)} tools loaded")
        print(f"[Init] SAFE_TOOLS: {len(SAFE_TOOLS)}, DANGEROUS_TOOLS: {len(DANGEROUS_TOOLS)}")

    def _list_tools(self):
        lines = ["\n--- Loaded Tools ---"]
        for name, tool in sorted(registry._tools.items()):
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
        lines.append(f"\n  Total: {len(registry._tools)} tools")
        return "\n".join(lines)

    def _show_config(self):
        return (
            f"\n--- Agent Config ---\n"
            f"  Provider: {self.provider_name}\n"
            f"  Model: {self.model_name}\n"
            f"  Session: {self.session_id}\n"
            f"  Tools loaded: {len(self.agent.internal_tools)}\n"
            f"  Skills loaded: {len(self.agent.skills)}\n"
            f"  Telemetry: {self.agent.telemetry_enabled}\n"
            f"  Max iterations: {self.agent.max_iterations}\n"
            f"  Agent type: {type(self.agent).__name__}\n"
            f"  Supports tools: {self.agent.supports_tools}\n"
        )

    def _show_memory_status(self):
        return (
            f"\n--- Memory Status ---\n"
            f"  Memory system has been removed.\n"
        )

    def _show_telemetry(self):
        try:
            t = self.agent.telemetry
            return f"\n--- Telemetry ---\n{t}\n" if isinstance(t, dict) else f"\n--- Telemetry ---\n{t}\n"
        except Exception as e:
            return f"\n--- Telemetry ---\nError: {e}\n"

    def _show_token_estimate(self, text: str = None):
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

    async def _execute_tool_test(self, tool_name: str):
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

    async def start(self):
        print(BANNER)
        print(HELP_TEXT)

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("\nYou: ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                await self._handle_command(user_input)
                continue

            print("\nAgent: ", end="", flush=True)
            try:
                response = await self.agent.chat(
                    user_input,
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

        if command == "/quit":
            print("Bye!")
            sys.exit(0)
        elif command == "/help":
            print(HELP_TEXT)
        elif command == "/tools":
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
            if arg in ("solo", "project"):
                self.agent.set_mode(arg)
                print(f"Switched to {arg} mode")
            else:
                print("Usage: /mode <solo|project>")
        elif command == "/skills":
            skills = self.agent.skills
            if skills:
                print(f"\nLoaded skills: {[s.name for s in skills]}")
            else:
                print("\nNo skills loaded")
        else:
            print(f"Unknown command: {command}. Type /help for available commands.")


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    model = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Starting with provider={provider}, model={model or 'default'}")
    bot = Chatbot(provider=provider, model=model)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
