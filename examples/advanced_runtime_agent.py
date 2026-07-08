"""
Advanced Runtime Examples - Tool Agent with Full Runtime Support

This example shows how to build a tool-heavy agent that uses all
runtime components for production-grade execution:
- Loop detection preventing infinite tool loops
- Context management for long conversations
- Tool scheduling with deduplication
- Full telemetry

Run: python examples/advanced_runtime_agent.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, Any, List

# Ensure workspace root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from logicore.agent.base import Agent
from logicore.providers.ollama_provider import OllamaProvider
from logicore.runtime import (
    AgentRuntime,
    RuntimeConfig,
    TurnStatus,
    AgentEvent,
    AgentEventType,
    ToolCallRequest,
    ToolCallStatus,
    RecoveryActionType,
)


# === Custom Tools for Demo ===
DEMO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function", 
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Perform mathematical calculations",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression"},
                },
                "required": ["expression"],
            },
        },
    },
]


async def demo_tool_executor(name: str, args: Dict[str, Any]) -> Any:
    """Demo tool executor that simulates tool responses."""
    if name == "search_web":
        query = args.get("query", "")
        return f"Search results for '{query}': [Result 1: Example info about {query}]"
    
    elif name == "get_weather":
        location = args.get("location", "Unknown")
        return f"Weather in {location}: Sunny, 22°C, Humidity 45%"
    
    elif name == "calculate":
        expr = args.get("expression", "0")
        try:
            # Safe eval for demo (use proper parser in production)
            result = eval(expr, {"__builtins__": {}}, {})
            return f"Result: {result}"
        except Exception as e:
            return f"Error: {e}"
    
    return f"Unknown tool: {name}"


class ToolAgent:
    """
    Production-grade tool agent using Logicore Runtime.
    
    Features:
    - Automatic loop detection and recovery
    - Tool deduplication (no repeated identical calls)
    - Turn budgeting
    - Context management
    - Full observability
    """
    
    def __init__(
        self,
        model_name: str = "llama3.2:latest",
        max_turns: int = 30,
    ):
        self.model_name = model_name
        
        # Create provider
        self.provider = OllamaProvider(model_name=model_name)
        
        # Create runtime config
        self.config = RuntimeConfig(
            max_turns=max_turns,
            max_history_messages=50,
        )
        
        # Stricter loop detection for tool-heavy agent
        self.config.loop_detection.tool_call_threshold = 3
        self.config.loop_detection.content_repetition_threshold = 5
        
        # Enable tool deduplication
        self.config.tool.enable_deduplication = True
        self.config.tool.cache_ttl_seconds = 60
        
        # Create runtime
        self.runtime = AgentRuntime(
            config=self.config,
            provider=self.provider,
            model_name=model_name,
            tool_executor=demo_tool_executor,
        )
        
        # Message history
        self.messages: List[Dict[str, Any]] = []
    
    async def chat(
        self,
        user_message: str,
        session_id: str = "default",
    ) -> str:
        """
        Process user message with full runtime support.
        
        Handles:
        - Turn management
        - Loop detection
        - Tool execution with deduplication
        - Context management
        """
        
        # Check budget
        if self.runtime.is_budget_exceeded(session_id):
            return "[Error] Turn budget exceeded. Please start a new session."
        
        # Add user message
        self.messages.append({
            "role": "user",
            "content": user_message,
        })
        
        # Execute within a turn
        async with self.runtime.turn(session_id) as turn:
            response_text = ""
            tool_loop_count = 0
            max_tool_iterations = 5
            
            while tool_loop_count < max_tool_iterations:
                # Manage context before LLM call
                ctx_result, managed_messages = await self.runtime.manage_context(
                    self.messages,
                    session_id,
                )
                
                if ctx_result.compressed:
                    print(f"[Context] Compressed, saved {ctx_result.tokens_saved} tokens")
                
                # Simulate LLM call (in real code, call provider.chat())
                # For demo, we'll generate a simple response with tool calls
                llm_response = self._simulate_llm_response(user_message, tool_loop_count)
                
                # Check if response contains tool calls
                if llm_response.get("tool_calls"):
                    tool_calls = llm_response["tool_calls"]
                    
                    # Check for loops before executing tools
                    for tc in tool_calls:
                        event = self.runtime.create_tool_call_event(
                            tool_name=tc["name"],
                            tool_args=tc.get("args", {}),
                        )
                        loop_result = await self.runtime.check_loop(event, session_id)
                        
                        if loop_result.detected:
                            print(f"[Loop] Detected: {loop_result.loop_type.value}")
                            action = self.runtime.get_recovery_action(loop_result)
                            
                            if action.action_type == RecoveryActionType.TOOL_COOLDOWN:
                                print(f"[Recovery] Cooling down tool: {action.tool_name}")
                                self.runtime.apply_tool_cooldown(
                                    session_id,
                                    action.tool_name,
                                    action.cooldown_seconds or 30,
                                )
                            
                            # Skip this tool call
                            continue
                    
                    # Execute tools through scheduler
                    results = await self.runtime.execute_tools(
                        [{"name": tc["name"], "args": tc.get("args", {})} for tc in tool_calls],
                        session_id,
                        turn.turn_id,
                    )
                    
                    # Add tool results to messages
                    for result in results:
                        if result.status == ToolCallStatus.DEDUPLICATED:
                            print(f"[Tool] {result.name} - deduplicated (reusing cached result)")
                        elif result.success:
                            print(f"[Tool] {result.name} - success")
                        else:
                            print(f"[Tool] {result.name} - error: {result.error}")
                        
                        self.messages.append({
                            "role": "tool",
                            "name": result.name,
                            "content": str(result.result) if result.success else f"Error: {result.error}",
                        })
                    
                    tool_loop_count += 1
                    turn.tool_calls += len(results)
                    
                else:
                    # No tool calls, we have the final response
                    response_text = llm_response.get("content", "")
                    break
            
            # Add assistant response
            if response_text:
                self.messages.append({
                    "role": "assistant",
                    "content": response_text,
                })
            
            return response_text
    
    def _simulate_llm_response(
        self,
        user_message: str,
        iteration: int,
    ) -> Dict[str, Any]:
        """Simulate LLM response (replace with real provider call)."""
        
        # First iteration: maybe call a tool
        if iteration == 0:
            if "weather" in user_message.lower():
                # Extract city (simple heuristic)
                words = user_message.split()
                city = words[-1] if words else "London"
                return {
                    "tool_calls": [
                        {"name": "get_weather", "args": {"location": city}}
                    ]
                }
            
            elif "search" in user_message.lower() or "find" in user_message.lower():
                return {
                    "tool_calls": [
                        {"name": "search_web", "args": {"query": user_message}}
                    ]
                }
            
            elif any(c in user_message for c in "+-*/="):
                return {
                    "tool_calls": [
                        {"name": "calculate", "args": {"expression": user_message}}
                    ]
                }
        
        # No tools needed, return text response
        return {
            "content": f"I understand you're asking about: {user_message}. Based on my knowledge, I can help with that."
        }
    
    def get_stats(self, session_id: str = "default") -> Dict[str, Any]:
        """Get session statistics."""
        metrics = self.runtime.get_session_metrics(session_id)
        return metrics.to_dict()
    
    def reset(self, session_id: str = "default"):
        """Reset session state."""
        self.runtime.clear_session(session_id)
        self.messages = []


async def main():
    print("=" * 60)
    print("  ADVANCED TOOL AGENT WITH RUNTIME")
    print("=" * 60)
    
    # Create agent
    agent = ToolAgent(
        model_name="llama3.2:latest",
        max_turns=20,
    )
    
    print("Agent initialized. Try these commands:")
    print("  - 'weather in Tokyo'")
    print("  - 'search for Python tutorials'")
    print("  - '2 + 2 * 3'")
    print("  - 'stats' - show statistics")
    print("  - 'reset' - reset session")
    print("  - 'quit' - exit")
    print("-" * 60)
    
    session_id = "demo-session"
    
    try:
        while True:
            user_input = input("\nYou: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ("quit", "exit", "q"):
                break
            
            if user_input.lower() == "stats":
                stats = agent.get_stats(session_id)
                print("\nSession Statistics:")
                print(json.dumps(stats, indent=2))
                continue
            
            if user_input.lower() == "reset":
                agent.reset(session_id)
                print("Session reset.")
                continue
            
            # Process message
            response = await agent.chat(user_input, session_id)
            print(f"\nAssistant: {response}")
            
    except KeyboardInterrupt:
        print("\nInterrupted.")
    
    finally:
        print("\n" + "=" * 60)
        print("Final Statistics:")
        print(json.dumps(agent.get_stats(session_id), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
