"""
Advanced Chatbot using Native Logicore Architecture

This example demonstrates how to build a production-grade chatbot
using native Logicore domain components with NEW FEATURES:

Core Components:
- logicore.agent.base.Agent - Main agent class with reasoning/tracking/planning
- logicore.runtime.loop_detection - Loop detection
- logicore.runtime.scheduler - Tool scheduling with dedup/retry
- logicore.context.TokenBudget - Context budget tracking

NEW Deep Reasoning & Planning Features:
- logicore.runtime.reasoning - 5-level reasoning depth (MINIMAL → DEEP)
- logicore.runtime.tracker - Hierarchical task tracking with dependencies
- logicore.runtime.planner - Plan-before-execute workflow with approval gates
- logicore.runtime.progress - Real-time progress tracking with ETA

Run: python examples/native_chatbot.py

Commands for testing new features:
  /level [1-5|minimal|low|medium|high|deep] - Set reasoning level
  /task create <title>    - Create a new task
  /task list              - List all tasks
  /task tree              - Visualize task hierarchy
  /task close <id>        - Close a task
  /plan enter             - Enter plan mode
  /plan submit <steps>    - Submit plan (comma-separated steps)
  /plan approve           - Approve current plan
  /plan view              - View current plan
  /plan exit              - Exit plan mode
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

# Ensure workspace root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

# === Native Logicore Imports ===
from logicore.agent.base import Agent
from logicore.providers.ollama_provider import OllamaProvider
from logicore.runtime.scheduler import ToolScheduler
from logicore.runtime import RuntimeConfig, ToolConfig
from logicore.runtime.loop_detection import LoopDetectionEngine, LoopDetectionResult
from logicore.context_engine.token_estimator import estimate_tokens, estimate_message_tokens
from logicore.runtime.context.token_budget import TokenBudget, TokenCategory

# === NEW: Runtime Feature Imports ===
from logicore.runtime.reasoning import ReasoningLevel, ReasoningController, ReasoningConfig
from logicore.runtime.tracker import TrackerService, TaskType, TaskStatus, TaskPriority
from logicore.runtime.planner import PlanService, PlanStatus
from logicore.runtime.progress import ProgressService


# === Configuration ===
DEFAULT_MODEL = "gpt-oss:20b-cloud"
SESSION_ID = "native-chatbot"
MAX_TURNS = 50

# Reasoning level display names
LEVEL_NAMES = {
    ReasoningLevel.MINIMAL: "⚡ MINIMAL (quick responses)",
    ReasoningLevel.LOW: "🔹 LOW (light analysis)",
    ReasoningLevel.MEDIUM: "🔸 MEDIUM (balanced)",
    ReasoningLevel.HIGH: "🔶 HIGH (thorough analysis)",
    ReasoningLevel.DEEP: "🧠 DEEP (maximum reasoning)",
}


class ChatSession:
    """
    Manages a chat session with advanced features:
    - Loop detection and budget tracking
    - 5-level reasoning depth control
    - Hierarchical task tracking
    - Plan-before-execute workflow
    - Real-time progress tracking
    """
    
    def __init__(
        self,
        agent: Agent,
        session_id: str,
        max_turns: int = 50,
        model_name: str = "default",
        work_dir: Optional[Path] = None,
    ):
        self.agent = agent
        self.session_id = session_id
        self.max_turns = max_turns
        self.work_dir = work_dir or Path(".")
        
        # Turn tracking
        self.turn_count = 0
        
        # Loop detection
        self.loop_detector = LoopDetectionEngine(RuntimeConfig())
        
        # Token budget
        self.budget = TokenBudget(
            config=RuntimeConfig.from_settings(),
            model_name=model_name,
        )
        
        # Tool scheduler (for future tool execution)
        self.scheduler = ToolScheduler(
            ToolConfig(enable_deduplication=True)
        )
        
        # === NEW: Reasoning Controller ===
        self.reasoning = ReasoningController(
            config=ReasoningConfig(
                level=ReasoningLevel.MEDIUM,
                auto_escalate=True,
                auto_escalate_keywords=["analyze", "debug", "architect", "design", "complex"],
            ),
        )
        self.reasoning.on_level_change(self._on_reasoning_change)
        
        # === NEW: Task Tracker ===
        self.tracker = TrackerService(
            project_dir=str(self.work_dir),
        )
        
        # === NEW: Plan Service ===
        self.planner = PlanService(
            project_dir=str(self.work_dir),
        )
        self._in_plan_mode: bool = False  # local flag since enter_plan_mode() is stateless
        
        # === NEW: Progress Service ===
        self.progress = ProgressService()
        self.progress.on_progress(self._on_progress)
    
    def _on_reasoning_change(self, old_level: ReasoningLevel, new_level: ReasoningLevel):
        """Callback when reasoning level changes."""
        print(f"\n[Reasoning] {LEVEL_NAMES[old_level]} → {LEVEL_NAMES[new_level]}")
    
    def _on_progress(self, event):
        """Callback for progress updates."""
        bar = self.progress.get_progress_bar(width=30)
        print(f"\r[Progress] {bar} {event.message}", end="", flush=True)
        if event.percent >= 100 or event.status == "failed":
            print()  # Newline when done
    
    @property
    def remaining_turns(self) -> int:
        return max(0, self.max_turns - self.turn_count)
    
    @property
    def is_budget_exceeded(self) -> bool:
        return self.turn_count >= self.max_turns
    
    # === NEW: Reasoning Level Commands ===
    def set_reasoning_level(self, level_input: str) -> str:
        """Set reasoning level by name or number."""
        level_map = {
            "1": ReasoningLevel.MINIMAL, "minimal": ReasoningLevel.MINIMAL,
            "2": ReasoningLevel.LOW, "low": ReasoningLevel.LOW,
            "3": ReasoningLevel.MEDIUM, "medium": ReasoningLevel.MEDIUM,
            "4": ReasoningLevel.HIGH, "high": ReasoningLevel.HIGH,
            "5": ReasoningLevel.DEEP, "deep": ReasoningLevel.DEEP,
        }
        level = level_map.get(level_input.lower())
        if level is None:
            return f"Invalid level. Use: 1-5 or minimal/low/medium/high/deep"
        
        self.reasoning.set_level(level)
        return f"Reasoning level set to: {LEVEL_NAMES[level]}"
    
    def get_reasoning_status(self) -> str:
        """Get current reasoning status."""
        level = self.reasoning.current_level
        budget = self.reasoning.config.get_thinking_budget_for_level()
        return (
            f"Current: {LEVEL_NAMES[level]}\n"
            f"Thinking Budget: {budget} tokens\n"
            f"Auto-escalate: {'ON' if self.reasoning.config.auto_escalate else 'OFF'}"
        )
    
    # === NEW: Task Tracking Commands ===
    def task_create(self, title: str, task_type: str = "task") -> str:
        """Create a new task."""
        type_map = {
            "epic": TaskType.EPIC,
            "task": TaskType.TASK,
            "subtask": TaskType.SUBTASK,
            "bug": TaskType.BUG,
        }
        tt = type_map.get(task_type.lower(), TaskType.TASK)
        task = self.tracker.create_task(title=title, type=tt)
        return f"Created {tt.value} [{task.id}]: {title}"
    
    def task_list(self, status_filter: Optional[str] = None) -> str:
        """List tasks with optional status filter."""
        status = None
        if status_filter:
            status_map = {
                "open": TaskStatus.OPEN,
                "in_progress": TaskStatus.IN_PROGRESS,
                "blocked": TaskStatus.BLOCKED,
                "closed": TaskStatus.CLOSED,
            }
            status = status_map.get(status_filter.lower())
        
        tasks = self.tracker.list_tasks(status=status)
        if not tasks:
            return "No tasks found."
        
        lines = ["Tasks:"]
        for t in tasks:
            status_icon = {"OPEN": "⬚", "IN_PROGRESS": "▶", "BLOCKED": "⛔", "CLOSED": "✓"}.get(t.status.value, "?")
            lines.append(f"  {status_icon} [{t.id}] {t.title} ({t.progress_percent}%)")
        return "\n".join(lines)
    
    def task_tree(self) -> str:
        """Visualize task hierarchy."""
        return self.tracker.visualize()
    
    def task_close(self, task_id: str) -> str:
        """Close a task."""
        result = self.tracker.close_task(task_id)
        if result:
            return f"Closed task [{task_id}]"
        return f"Cannot close task [{task_id}] - check dependencies"
    
    def task_start(self, task_id: str) -> str:
        """Start working on a task."""
        result = self.tracker.start_task(task_id)
        if result:
            return f"Started task [{task_id}]"
        return f"Failed to start task [{task_id}]"
    
    # === NEW: Plan Mode Commands ===
    def plan_enter(self, goal: str = "Current task") -> str:
        """Enter plan mode."""
        self.planner.enter_plan_mode(reason=goal)
        self._in_plan_mode = True
        return f"📋 PLAN MODE ACTIVE\nGoal: {goal}\nUse '/plan submit <steps>' to create a plan"
    
    def plan_submit(self, steps_str: str) -> str:
        """Submit a plan with comma-separated steps."""
        if not self._in_plan_mode:
            return "Not in plan mode. Use '/plan enter' first."
        
        steps = [s.strip() for s in steps_str.split(",") if s.strip()]
        if not steps:
            return "No steps provided. Use: /plan submit step1, step2, step3"
        
        plan = self.planner.create_plan(
            title="User Plan",
            description="Plan created via chat",
            steps=[{"description": s} for s in steps],
        )
        self.planner.submit_plan(plan.id)
        
        lines = [f"📋 Plan submitted [{plan.id}]:", "Steps:"]
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"  {i}. {step.description}")
        lines.append("\nUse '/plan approve' to approve or '/plan exit' to cancel")
        return "\n".join(lines)
    
    def plan_approve(self) -> str:
        """Approve the pending plan."""
        plans = self.planner.list_plans(status=PlanStatus.PENDING)
        if not plans:
            return "No pending plans to approve."
        
        plan = plans[0]
        self.planner.approve_plan(plan.id)
        return f"✅ Plan [{plan.id}] APPROVED\nReady for execution!"
    
    def plan_view(self) -> str:
        """View current plan status."""
        plans = self.planner.list_plans()
        if not plans:
            return "No plans created."
        
        plan = plans[-1]  # Most recent
        return self.planner.visualize_plan(plan.id)
    
    def plan_exit(self) -> str:
        """Exit plan mode."""
        self.planner.exit_plan_mode()
        self._in_plan_mode = False
        return "Exited plan mode."
    
    async def chat(self, user_input: str) -> Optional[str]:
        """Process a chat message with full safety features and reasoning."""
        
        # Check turn budget
        if self.is_budget_exceeded:
            return "[Session] Turn budget exceeded. Type 'reset' to continue."
        
        self.turn_count += 1
        
        # === NEW: Auto-adjust reasoning level based on query complexity ===
        old_level = self.reasoning.current_level
        self.reasoning.adjust_for_query(user_input)
        new_level = self.reasoning.current_level
        
        # Show reasoning header
        level_icon = {1: "⚡", 2: "🔹", 3: "🔸", 4: "🔶", 5: "🧠"}.get(new_level.value, "")
        print(f"[Turn {self.turn_count}/{self.max_turns}] {level_icon} Reasoning: {new_level.name}")
        
        try:
            # === NEW: Add reasoning prompt addon ===
            reasoning_addon = self.reasoning.get_system_prompt_addon()
            
            # Get response with reasoning context
            print("Assistant: ", end="", flush=True)
            response = await self.agent.chat(
                user_input,
                session_id=self.session_id,
                stream=True,
            )
            print()  # Newline after stream
            
            # Check for content loops
            if response:
                loop_result = self.loop_detector.check_content(self.session_id, response)
                if loop_result.detected:
                    print(f"\n[Loop] Detected: {loop_result.message}")
                    recovery_msg = loop_result.get_recovery_message()
                    if recovery_msg:
                        print(f"[Recovery] {recovery_msg}")
            
            # Update token budget estimate
            # (In production, use actual token counts from provider)
            estimated = estimate_message_tokens([{"role": "user", "content": user_input}])
            estimated += estimate_message_tokens([{"role": "assistant", "content": response or ""}])
            from logicore.runtime.context.token_budget import TokenCategory
            self.budget.add_tokens(TokenCategory.MESSAGES, estimated)
            
            if self.budget.should_warn():
                print(f"[Budget] Warning: {self.budget.get_usage_ratio()*100:.1f}% of context used")
            
            return response
            
        except Exception as e:
            print(f"\n[Error] {e}")
            return None
    
    def reset(self):
        """Reset session state including new features."""
        self.turn_count = 0
        self.loop_detector.reset(self.session_id)
        self.budget.reset()
        self.scheduler.clear()
        self.reasoning.reset()
        self.progress.reset()
        print("Session reset (including reasoning, progress).")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get session statistics including new features."""
        task_summary = self.tracker.get_summary()
        return {
            "turns": self.turn_count,
            "remaining": self.remaining_turns,
            "reasoning": self.reasoning.get_state_summary(),
            "tasks": task_summary,
            "plan_mode": self._in_plan_mode,
            "budget": self.budget.to_dict(),
            "scheduler": self.scheduler.get_statistics(),
        }


def print_help():
    """Print available commands."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                    AVAILABLE COMMANDS                        ║
╠══════════════════════════════════════════════════════════════╣
║ GENERAL:                                                     ║
║   quit, exit, q     - Exit chatbot                           ║
║   stats             - Show session statistics                ║
║   reset             - Reset session state                    ║
║   help              - Show this help                         ║
╠══════════════════════════════════════════════════════════════╣
║ REASONING LEVEL (1-5 slider):                                ║
║   /level            - Show current reasoning level           ║
║   /level 1-5        - Set level by number                    ║
║   /level minimal    - Quick responses                        ║
║   /level low        - Light analysis                         ║
║   /level medium     - Balanced (default)                     ║
║   /level high       - Thorough analysis                      ║
║   /level deep       - Maximum reasoning                      ║
╠══════════════════════════════════════════════════════════════╣
║ TASK TRACKING:                                               ║
║   /task create <title>     - Create new task                 ║
║   /task list [status]      - List tasks (open/closed/etc)    ║
║   /task tree               - Visualize task hierarchy        ║
║   /task start <id>         - Start working on task           ║
║   /task close <id>         - Close a task                    ║
╠══════════════════════════════════════════════════════════════╣
║ PLAN MODE:                                                   ║
║   /plan enter [goal]       - Enter plan mode                 ║
║   /plan submit s1,s2,s3    - Submit plan steps               ║
║   /plan approve            - Approve pending plan            ║
║   /plan view               - View current plan               ║
║   /plan exit               - Exit plan mode                  ║
╚══════════════════════════════════════════════════════════════╝
""")


def handle_command(session: ChatSession, user_input: str) -> Optional[str]:
    """Handle slash commands. Returns response string or None if not a command."""
    if not user_input.startswith("/"):
        return None
    
    parts = user_input[1:].split(maxsplit=2)
    if not parts:
        return "Invalid command. Type 'help' for available commands."
    
    cmd = parts[0].lower()
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""
    
    # === Reasoning Level Commands ===
    if cmd == "level":
        if not arg1:
            return session.get_reasoning_status()
        return session.set_reasoning_level(arg1)
    
    # === Task Commands ===
    elif cmd == "task":
        if arg1 == "create" and arg2:
            return session.task_create(arg2)
        elif arg1 == "list":
            return session.task_list(arg2 if arg2 else None)
        elif arg1 == "tree":
            return session.task_tree()
        elif arg1 == "start" and arg2:
            return session.task_start(arg2)
        elif arg1 == "close" and arg2:
            return session.task_close(arg2)
        else:
            return "Usage: /task create|list|tree|start|close [args]"
    
    # === Plan Commands ===
    elif cmd == "plan":
        if arg1 == "enter":
            return session.plan_enter(arg2 if arg2 else "Current task")
        elif arg1 == "submit" and arg2:
            return session.plan_submit(arg2)
        elif arg1 == "approve":
            return session.plan_approve()
        elif arg1 == "view":
            return session.plan_view()
        elif arg1 == "exit":
            return session.plan_exit()
        else:
            return "Usage: /plan enter|submit|approve|view|exit [args]"
    
    else:
        return f"Unknown command: /{cmd}. Type 'help' for available commands."


async def main():
    print("=" * 64)
    print("  🚀 ADVANCED LOGICORE CHATBOT")
    print("  Features: Reasoning Levels | Task Tracking | Plan Mode")
    print("=" * 64)
    
    # Get model
    model = input(f"Model name [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
    
    print(f"\nInitializing with model: {model}")
    print("Type 'help' for commands, or start chatting!")
    print("-" * 64)
    
    # Create provider
    provider = OllamaProvider(model_name=model)
    
    # Create agent with NEW features enabled
    agent = Agent(
        llm=provider,
        system_message="You are a helpful assistant. Be concise but thorough when needed.",
        tools=True,
        debug=False,
        memory=False,
        # === NEW: Enable runtime features ===
        reasoning_level="medium",
        task_tracking=True,
        plan_mode=True,
    )
    
    # Stream tokens to stdout
    def on_token(token: str):
        if token:
            sys.stdout.write(token)
            sys.stdout.flush()
    
    agent.set_callbacks(on_token=on_token)
    
    # Create session with work directory
    session = ChatSession(
        agent=agent,
        session_id=SESSION_ID,
        max_turns=MAX_TURNS,
        model_name=model,
        work_dir=Path("."),
    )
    
    # Show initial status
    print(f"\n{LEVEL_NAMES[session.reasoning.current_level]}")
    print(f"Turn budget: {session.remaining_turns}")
    print()
    
    try:
        while True:
            try:
                # Show plan mode indicator in prompt
                prompt = "📋 Plan> " if session._in_plan_mode else "You: "
                user_input = input(prompt).strip()
            except EOFError:
                break
            
            if not user_input:
                continue
            
            # Basic commands
            if user_input.lower() in ("quit", "exit", "q"):
                print("\nGoodbye!")
                break
            
            if user_input.lower() == "help":
                print_help()
                continue
            
            if user_input.lower() == "stats":
                import json
                print("\n📊 Session Statistics:")
                print(json.dumps(session.get_stats(), indent=2, default=str))
                continue
            
            if user_input.lower() == "reset":
                session.reset()
                continue
            
            # Handle slash commands
            cmd_result = handle_command(session, user_input)
            if cmd_result is not None:
                print(cmd_result)
                continue
            
            # Regular chat
            await session.chat(user_input)
            
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    
    finally:
        print("\n" + "=" * 64)
        print("📊 Final Statistics:")
        import json
        print(json.dumps(session.get_stats(), indent=2, default=str))
        
        if hasattr(agent, "cleanup"):
            await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
