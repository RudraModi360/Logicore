"""
Logicore Power Chatbot

A fully-featured interactive chatbot that uses Logicore's native modules directly:

Under the hood (all happening transparently):
  - logicore.agents.LoopDetector       → prevents infinite tool/content loops
  - logicore.tools.ToolScheduler       → deduplicates tool calls, retries failures
  - logicore.context_engine            → context management with masking/compression
  - logicore.telemetry.TelemetryTracker → token usage, latency, cache stats

Run:
    python examples/power_chatbot.py

Supports providers: ollama / gemini / groq
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Native Logicore imports ──────────────────────────────────────────────────
from logicore.agent.base import Agent
from logicore.runtime.context.token_estimator import (
    get_model_context_window, estimate_tokens, estimate_message_tokens,
)
from logicore.runtime.context.token_budget import TokenCategory
from logicore.runtime.scheduler import ToolScheduler
# ─────────────────────────────────────────────────────────────────────────────

# Provider imports (only what's needed gets used at runtime)
try:
    from logicore.providers.ollama_provider import OllamaProvider
except ImportError:
    OllamaProvider = None

try:
    from logicore.providers.gemini_provider import GeminiProvider
except ImportError:
    GeminiProvider = None

try:
    from logicore.providers.groq_provider import GroqProvider
except ImportError:
    GroqProvider = None


# ── Banner ───────────────────────────────────────────────────────────────────

BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║             LOGICORE  POWER  CHATBOT                     ║
║                                                          ║
║  Under the hood:                                         ║
║   • LoopDetector   — stops repetitive tool/content loops ║
║   • ToolScheduler  — dedup + retry with backoff          ║
║   • TokenBudget    — model-aware context tracking        ║
║   • ContextEngine  — masking/compression/truncation      ║
║   • TelemetryTracker — live latency + token stats        ║
╚══════════════════════════════════════════════════════════╝
"""

HELP = """
Special commands:
  stats    — show live session stats (turns, tokens, loops, tools)
  budget   — show context window usage
  tools    — list available tools
  reset    — clear session and restart
  help     — show this message
  quit     — exit
"""


# ── Provider selection ───────────────────────────────────────────────────────

def select_provider():
    """Interactive provider + model selection. Returns (provider, model_name)."""

    print("Provider options:")
    print("  1. ollama  (local, default)")
    print("  2. gemini")
    print("  3. groq")

    choice = input("\nSelect provider [1/2/3, default=1]: ").strip() or "1"

    if choice in ("2", "gemini"):
        if GeminiProvider is None:
            print("GeminiProvider not available. Falling back to ollama.")
            choice = "1"
        else:
            model = input("Gemini model [gemini-2.5-pro]: ").strip() or "gemini-2.5-pro"
            api_key = input("Gemini API key (or set GEMINI_API_KEY env): ").strip() or None
            import os
            api_key = api_key or os.getenv("GEMINI_API_KEY")
            return GeminiProvider(model_name=model, api_key=api_key), model

    if choice in ("3", "groq"):
        if GroqProvider is None:
            print("GroqProvider not available. Falling back to ollama.")
            choice = "1"
        else:
            model = input("Groq model [moonshotai/kimi-k2-instruct]: ").strip() or "moonshotai/kimi-k2-instruct"
            return GroqProvider(model_name=model), model

    # ollama default
    model = input("Ollama model [gpt-oss:20b-cloud]: ").strip() or "gpt-oss:20b-cloud"
    return OllamaProvider(model_name=model), model


# ── Session ──────────────────────────────────────────────────────────────────

class PowerChatSession:
    """
    Wraps an Agent with native Logicore components for production-grade chat.

    What's happening under the hood each turn:
      1. Turn counter + budget guard
      2. LoopDetector checks content of each response for repetition patterns
      3. ToolScheduler handles dedup/retry for any tool calls fired
      4. TokenBudget estimates context fill after each message
      5. ContextEngine auto-compresses when history gets too long
      6. TelemetryTracker provides per-turn token + latency stats
    """

    def __init__(self, agent: Agent, model_name: str, max_turns: int = 80):
        self.agent = agent
        self.model_name = model_name
        self.session_id = "power-chat"
        self.max_turns = max_turns

        # ── native modules ───────────────────────────────────────────
        self.loop_detector = LoopDetector(
            tool_threshold=5,       # flag after 5 identical consecutive tool calls
            content_threshold=8,    # flag after 8 repeated content chunks
        )

        self.budget = TokenBudget(
            config=RuntimeConfig.from_settings(),
            model_name=model_name,
        )

        self.scheduler = ToolScheduler(SchedulerConfig(
            enable_deduplication=True,
            cache_ttl_seconds=120,
            max_retries=3,
            use_exponential_backoff=True,
        ))
        # ─────────────────────────────────────────────────────────────

        # Turn stats
        self.turn_count = 0
        self.loop_events: list = []
        self._stream_tokens = 0
        self._first_token_ts: Optional[float] = None

    # ── streaming token hook ─────────────────────────────────────────────────
    def _on_token(self, token: str) -> None:
        if token:
            if self._first_token_ts is None:
                self._first_token_ts = time.perf_counter()
            self._stream_tokens += 1
            sys.stdout.write(token)
            sys.stdout.flush()

    # ── main chat ────────────────────────────────────────────────────────────
    async def chat(self, user_input: str) -> Optional[str]:
        if self.turn_count >= self.max_turns:
            print(f"\n[Budget] Turn limit ({self.max_turns}) reached. Type 'reset' to continue.")
            return None

        self.turn_count += 1
        self._stream_tokens = 0
        self._first_token_ts = None
        turn_start = time.perf_counter()

        print(f"\n[Turn {self.turn_count}/{self.max_turns}  |  "
              f"ctx {self.budget.usage_percent:.0f}%  |  "
              f"remaining {self.budget.remaining:,} tok]")
        print("Assistant: ", end="", flush=True)

        try:
            response = await self.agent.chat(
                user_input,
                session_id=self.session_id,
                stream=True,
            )
        except Exception as exc:
            print(f"\n[Error] {exc}")
            return None

        turn_end = time.perf_counter()
        print()  # newline after stream

        if not response:
            return None

        # ── Loop detection on response content ───────────────────────
        loop_result = self.loop_detector.check_content(self.session_id, response)
        if loop_result.detected:
            self.loop_events.append(loop_result)
            print(f"\n⚠  [LoopDetector] {loop_result.message}")
            recovery = loop_result.get_recovery_message()
            if recovery:
                print(f"   Hint: {recovery}")

        # ── Token budget update (character-based estimate) ────────────
        user_tokens = estimate_message_tokens([{"role": "user", "content": user_input}])
        asst_tokens = estimate_message_tokens([{"role": "assistant", "content": response}])
        self.budget.add_tokens(TokenCategory.MESSAGES, user_tokens + asst_tokens)

        if self.budget.should_warn() and not self.budget.should_compress():
            print(f"\n⚠  [TokenBudget] Context at {self.budget.get_usage_ratio()*100:.1f}% — "
                  f"{self.budget.get_remaining_tokens():,} tokens remaining.")

        if self.budget.should_compress():
            print(f"\n🔄 [TokenBudget] Context at {self.budget.get_usage_ratio()*100:.1f}% — "
                  f"ContextEngine will compress on next turn.")

        # ── Mini per-turn stats ───────────────────────────────────────
        latency_ms = (turn_end - turn_start) * 1000
        ttft_ms = (
            (self._first_token_ts - turn_start) * 1000
            if self._first_token_ts else None
        )
        ttft_str = f"  TTFT {ttft_ms:.0f}ms" if ttft_ms else ""
        print(f"   ⏱  {latency_ms:.0f}ms{ttft_str}  |  "
              f"~{user_tokens + asst_tokens} tok this turn")

        return response

    # ── commands ─────────────────────────────────────────────────────────────
    def show_stats(self) -> None:
        tele = self.agent.telemetry if self.agent.telemetry_enabled else {}
        sched_stats = self.scheduler.get_statistics()

        in_tok  = tele.get("total_input_tokens", "n/a")
        out_tok = tele.get("total_output_tokens", "n/a")
        tot_tok = tele.get("total_tokens", "n/a")

        print(f"""
┌─ Session Stats ─────────────────────────────────────────
│  Turns          : {self.turn_count} / {self.max_turns}
│  Loop events    : {len(self.loop_events)}
├─ Context Budget ────────────────────────────────────────
│  Model          : {self.model_name}
│  Context window : {self.budget.context_window:,} tokens
│  Used (est.)    : {self.budget.get_usage().total:,} tokens  ({self.budget.get_usage_ratio()*100:.1f}%)
│  Remaining      : {self.budget.get_remaining_tokens():,} tokens
├─ Tool Scheduler ────────────────────────────────────────
│  Total calls    : {sched_stats.get('total', 0)}
│  Deduped        : {sched_stats.get('skipped_dedup', 0)}
│  Failed         : {sched_stats.get('failed', 0)}
├─ Telemetry ─────────────────────────────────────────────
│  Input tokens   : {in_tok}
│  Output tokens  : {out_tok}
│  Total tokens   : {tot_tok}
└─────────────────────────────────────────────────────────""")

    def show_budget(self) -> None:
        usage = self.budget.get_usage()
        remaining = self.budget.get_remaining_tokens()
        ratio = self.budget.get_usage_ratio()
        print(f"""
Context Window Budget  ({self.model_name})
  Window    : {self.budget.context_window:>10,} tokens
  Used      : {usage.total:>10,} tokens  ({ratio*100:.1f}%)
  Remaining : {remaining:>10,} tokens
  Compress? : {self.budget.should_compress()}   Warn? {self.budget.should_warn()}

  By category:
    messages     : {usage.by_category.get(TokenCategory.MESSAGES, 0):>8,}
    tool_results : {usage.by_category.get(TokenCategory.TOOL_RESULTS, 0):>8,}
    system       : {usage.by_category.get(TokenCategory.SYSTEM, 0):>8,}
    tools        : {usage.by_category.get(TokenCategory.TOOLS, 0):>8,}""")

    async def show_tools(self) -> None:
        tools = await self.agent.get_all_tools()
        if not tools:
            print("  No tools loaded.")
            return
        print(f"\nAvailable tools ({len(tools)}):")
        for t in tools:
            fn = t.get("function", {}) if isinstance(t, dict) else {}
            name = fn.get("name", str(t))
            desc = fn.get("description", "")[:60]
            print(f"  • {name:<28} {desc}")

    def reset(self) -> None:
        self.turn_count = 0
        self.loop_events.clear()
        self.budget.reset()
        self.scheduler.clear()
        self.loop_detector.reset(self.session_id)
        # Clear agent session history
        if self.session_id in self.agent.sessions:
            self.agent.sessions[self.session_id].clear_history(keep_system=True)
        print("✓ Session reset — history cleared, budget reset.")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(BANNER)

    show_telemetry = input("Enable per-turn telemetry? [Y/n]: ").strip().lower() not in ("n", "no")
    provider, model_name = select_provider()
    max_turns = 80

    print(f"\nInitialising  model={model_name}  "
          f"context={get_model_context_window(model_name):,} tokens\n")

    agent = Agent(
        provider=provider,
        system_prompt=(
            "You are a helpful, precise assistant. "
            "Use tools when needed. Think step-by-step for complex tasks."
        ),
        tools=True,
        telemetry=show_telemetry,

        debug=False,
    )

    session = PowerChatSession(agent, model_name, max_turns=max_turns)
    agent.set_callbacks(on_token=session._on_token)

    print(f"Ready. Context window: {session.budget.context_window:,} tokens.")
    print(f"Type 'help' for commands, 'quit' to exit.\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            low = user_input.lower()

            if low in ("quit", "exit", "q"):
                print("\nGoodbye!")
                break
            elif low == "help":
                print(HELP)
            elif low == "stats":
                session.show_stats()
            elif low == "budget":
                session.show_budget()
            elif low == "tools":
                await session.show_tools()
            elif low == "reset":
                session.reset()
            else:
                await session.chat(user_input)

    except KeyboardInterrupt:
        print("\n\nInterrupted.")

    finally:
        print("\n" + "─" * 60)
        session.show_stats()
        if hasattr(agent, "cleanup"):
            await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
