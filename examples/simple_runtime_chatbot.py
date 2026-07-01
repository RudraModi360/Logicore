"""
Simple Chatbot using Logicore Runtime Framework

This example demonstrates how to build a production-grade chatbot
using the Logicore runtime components:
- AgentRuntime for orchestration
- TurnManager for bounded execution
- LoopDetectionEngine for preventing infinite loops
- ToolScheduler for tool execution
- TelemetryCollector for observability

Run: python examples/simple_runtime_chatbot.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure workspace root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

# === Logicore Imports ===
from logicore.agent.base import Agent
from logicore.providers.ollama_provider import OllamaProvider
from logicore.runtime import (
    AgentRuntime,
    RuntimeConfig,
    TurnStatus,
    AgentEvent,
    AgentEventType,
    RecoveryActionType,
)


# === Configuration ===
DEFAULT_MODEL = "gpt-oss:20b-cloud"
SESSION_ID = "chatbot-session"
MAX_TURNS = 50  # Maximum turns before session ends


async def create_tool_executor(agent: Agent):
    """Create a tool executor function that uses the agent's tools."""
    async def executor(name: str, args: Dict[str, Any]) -> Any:
        # Get all available tools
        tools = await agent.get_all_tools()
        
        # Find and execute the tool
        for tool in tools:
            if tool.name == name:
                return await tool.execute(**args)
        
        raise ValueError(f"Tool '{name}' not found")
    
    return executor


async def main():
    print("=" * 60)
    print("  LOGICORE RUNTIME CHATBOT")
    print("=" * 60)
    
    # === Step 1: Get user configuration ===
    model = input(f"Model name [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
    
    print(f"\nInitializing with model: {model}")
    print("Type 'quit' or 'exit' to stop")
    print("Type 'stats' to see session statistics")
    print("Type 'reset' to reset the session")
    print("-" * 60)
    
    # === Step 2: Create LLM provider ===
    provider = OllamaProvider(model_name=model)
    
    # === Step 3: Create Agent with tools ===
    agent = Agent(
        llm=provider,
        system_message="You are a helpful assistant. Be concise and clear.",
        tools=True,  # Enable built-in tools
        debug=False,
        memory=False,
    )
    
    # Stream tokens to stdout as they arrive
    def _on_token(token: str) -> None:
        if token:
            sys.stdout.write(token)
            sys.stdout.flush()
    
    agent.set_callbacks(on_token=_on_token)
    
    # === Step 4: Create Runtime with custom config ===
    config = RuntimeConfig(
        max_turns=MAX_TURNS,
        max_history_messages=100,
        debug=False,
    )
    config.loop_detection.enabled = True
    config.loop_detection.tool_call_threshold = 5
    config.telemetry.enabled = True
    
    # Create tool executor
    tool_executor = await create_tool_executor(agent)
    
    # Create runtime
    runtime = AgentRuntime(
        config=config,
        llm_provider=provider,
        model_name=model,
        tool_executor=tool_executor,
    )
    
    print(f"Runtime initialized. Turn budget: {runtime.get_remaining_turns(SESSION_ID)}")
    print()
    
    # === Step 5: Chat loop ===
    try:
        while True:
            # Get user input
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break
            
            if not user_input:
                continue
            
            # Handle special commands
            if user_input.lower() in ("quit", "exit", "q"):
                print("\nGoodbye!")
                break
            
            if user_input.lower() == "stats":
                _print_stats(runtime, SESSION_ID)
                continue
            
            if user_input.lower() == "reset":
                runtime.clear_session(SESSION_ID)
                print("Session reset. Turn budget restored.")
                continue
            
            # === Execute with Runtime ===
            await process_turn(runtime, agent, user_input, SESSION_ID)
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    
    finally:
        # Cleanup
        print("\n" + "=" * 60)
        print("Session Summary")
        print("=" * 60)
        _print_stats(runtime, SESSION_ID)
        
        if hasattr(agent, "cleanup"):
            await agent.cleanup()


async def process_turn(
    runtime: AgentRuntime,
    agent: Agent,
    user_input: str,
    session_id: str,
) -> Optional[str]:
    """Process a single chat turn with full runtime support."""
    
    # Check if we have turn budget
    if runtime.is_budget_exceeded(session_id):
        print("\n[Runtime] Turn budget exceeded. Type 'reset' to continue.")
        return None
    
    remaining = runtime.get_remaining_turns(session_id)
    
    # Start a turn
    async with runtime.turn(session_id) as turn:
        print(f"[Turn {turn.turn_number}/{runtime.config.max_turns}]")
        
        # Get response from agent
        print("Assistant: ", end="", flush=True)
        
        try:
            response = await agent.chat(
                user_input,
                session_id=session_id,
                stream=True,
            )
            print()  # Newline after streamed response
            
            # Check for loops after response
            if response:
                event = runtime.create_content_event(response)
                loop_result = await runtime.check_loop(event, session_id)
                
                if loop_result.detected:
                    print(f"\n[Runtime] Loop detected: {loop_result.loop_type.value}")
                    
                    # Get recovery action
                    action = runtime.get_recovery_action(loop_result)
                    
                    if action.action_type == RecoveryActionType.GUIDANCE:
                        print(f"[Runtime] Suggestion: {action.message}")
                    elif action.action_type == RecoveryActionType.TERMINATE:
                        print("[Runtime] Session terminated due to loop.")
                        return None
            
            # Update turn metrics
            turn.tool_calls = getattr(agent, "_last_tool_count", 0)
            
            return response
            
        except Exception as e:
            print(f"\n[Error] {e}")
            return None


def _print_stats(runtime: AgentRuntime, session_id: str):
    """Print session statistics."""
    metrics = runtime.get_session_metrics(session_id)
    loop_stats = runtime.get_loop_statistics()
    
    print(f"""
Session Statistics:
  Turns completed : {metrics.turns_completed}
  Turns failed    : {metrics.turns_failed}
  Remaining budget: {runtime.get_remaining_turns(session_id)}
  
Tool Execution:
  Total calls     : {metrics.tool_calls_total}
  Successful      : {metrics.tool_calls_success}
  Failed          : {metrics.tool_calls_error}
  Deduplicated    : {metrics.tool_calls_deduplicated}
  
Loop Detection:
  Loops detected  : {metrics.loops_detected}
  Recovery attempts: {metrics.recovery_attempts}
  
Context Management:
  Compressions    : {metrics.compressions}
  Tokens saved    : {metrics.tokens_compressed}
""")


if __name__ == "__main__":
    asyncio.run(main())
