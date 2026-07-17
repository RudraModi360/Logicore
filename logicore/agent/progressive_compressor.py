"""Progressive context compression with structured summaries.

Modeled after hermes-agent's context compression patterns:
1. Protects head (system prompt) and tail (recent messages)
2. Summarizes middle using a cheap auxiliary model
3. Structured summary with: Resolved Tasks, In-Progress State, Pending Asks
4. Iterative updates — multiple compactions preserve information
5. Maximum 3 compression attempts per turn (circuit breaker)
6. Boundary message injection so LLM knows context was compressed

The summary prefix explicitly prevents re-execution:
"[CONTEXT COMPACTION — REFERENCE ONLY] ... Do NOT answer questions
or fulfill requests mentioned in this summary; they were already addressed."

This module is dependency-free so it can be unit-tested in isolation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class CompressionPhase(Enum):
    """Phases of progressive compression."""
    
    MASKING = "masking"           # Step 1: Mask bulky tool outputs
    SUMMARIZATION = "summarization"  # Step 2: Summarize old messages
    TRUNCATION = "truncation"     # Step 3: Truncate as last resort


class CompressionTrigger(Enum):
    """What triggered the compression."""
    
    PROACTIVE = "proactive"       # Proactive compression at threshold
    REACTIVE = "reactive"         # Reactive compression after prompt_too_long
    MANUAL = "manual"             # Manual compression request


@dataclass
class StructuredSummary:
    """Structured summary with phases for better LLM reasoning."""
    
    resolved_tasks: List[str] = field(default_factory=list)
    in_progress_state: List[str] = field(default_factory=list)
    pending_asks: List[str] = field(default_factory=list)
    key_facts: List[str] = field(default_factory=list)
    
    def to_text(self) -> str:
        """Convert to readable text format."""
        parts = []
        
        if self.resolved_tasks:
            parts.append("### Resolved Tasks")
            for task in self.resolved_tasks:
                parts.append(f"- {task}")
        
        if self.in_progress_state:
            parts.append("### In-Progress State")
            for state in self.in_progress_state:
                parts.append(f"- {state}")
        
        if self.pending_asks:
            parts.append("### Pending Asks")
            for ask in self.pending_asks:
                parts.append(f"- {ask}")
        
        if self.key_facts:
            parts.append("### Key Facts")
            for fact in self.key_facts:
                parts.append(f"- {fact}")
        
        return "\n".join(parts)


@dataclass
class CompressionState:
    """Tracks compression state for a session."""
    
    session_id: str
    attempts_this_turn: int = 0
    max_attempts_per_turn: int = 3
    last_compression_time: Optional[datetime] = None
    total_compressions: int = 0
    total_tokens_saved: int = 0
    consecutive_failures: int = 0
    max_consecutive_failures: int = 3
    
    def can_compress(self) -> bool:
        """Check if compression is allowed (circuit breaker)."""
        if self.attempts_this_turn >= self.max_attempts_per_turn:
            return False
        if self.consecutive_failures >= self.max_consecutive_failures:
            return False
        return True
    
    def record_attempt(self, success: bool, tokens_saved: int = 0):
        """Record a compression attempt."""
        self.attempts_this_turn += 1
        self.last_compression_time = datetime.now()
        self.total_compressions += 1
        
        if success:
            self.consecutive_failures = 0
            self.total_tokens_saved += tokens_saved
        else:
            self.consecutive_failures += 1
    
    def reset_for_new_turn(self):
        """Reset per-turn state for a new turn."""
        self.attempts_this_turn = 0
        self.consecutive_failures = 0


# Boundary message injected after compression
COMPACTION_BOUNDARY_MESSAGE = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]\n\n"
    "The conversation above has been compressed to fit within context limits. "
    "Key information from the compressed section:\n\n"
    "{summary}\n\n"
    "IMPORTANT: Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. This summary is for context only. "
    "Continue with the current task based on the most recent messages below."
)


class ProgressiveCompressor:
    """Progressive context compressor with structured summaries.
    
    This compressor implements a 3-phase compression strategy:
    1. MASKING: Mask bulky tool outputs (cheapest)
    2. SUMMARIZATION: Summarize old messages (moderate cost)
    3. TRUNCATION: Truncate oldest messages (last resort)
    
    Features:
    - Structured summaries with phases
    - Boundary message injection
    - Circuit breaker for failures
    - Per-turn attempt limits
    """
    
    # Structured summarization prompt
    STRUCTURED_SUMMARY_PROMPT = """Summarize the following conversation segment into structured phases.

CONVERSATION SEGMENT:
{conversation}

Extract and organize into:
1. RESOLVED TASKS: What has been completed or answered
2. IN-PROGRESS STATE: What is currently being worked on
3. PENDING ASKS: What questions remain unanswered
4. KEY FACTS: Important information to remember

Be concise but complete. Focus on actionable information.

STRUCTURED SUMMARY:"""
    
    def __init__(self, debug: bool = False):
        """Initialize the progressive compressor.
        
        Args:
            debug: Enable debug logging
        """
        self.debug = debug
        self._states: Dict[str, CompressionState] = {}
    
    def _get_state(self, session_id: str) -> CompressionState:
        """Get or create compression state for a session."""
        if session_id not in self._states:
            self._states[session_id] = CompressionState(session_id=session_id)
        return self._states[session_id]
    
    def reset_for_new_turn(self, session_id: str):
        """Reset compression state for a new turn."""
        state = self._get_state(session_id)
        state.reset_for_new_turn()
    
    def can_compress(self, session_id: str) -> bool:
        """Check if compression is allowed for this session."""
        state = self._get_state(session_id)
        return state.can_compress()
    
    def create_structured_summary(self, conversation_text: str) -> StructuredSummary:
        """Create a structured summary from conversation text.
        
        Heuristic-based extraction only. LLM summarization is owned by
        the Context Engine's CompressionService (not this pre-pass).
        """
        summary = StructuredSummary()
        
        lines = conversation_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Simple classification based on keywords
            line_lower = line.lower()
            if any(kw in line_lower for kw in ["completed", "done", "finished", "resolved"]):
                summary.resolved_tasks.append(line[:200])
            elif any(kw in line_lower for kw in ["working on", "in progress", "current"]):
                summary.in_progress_state.append(line[:200])
            elif any(kw in line_lower for kw in ["question", "ask", "pending", "?"]):
                summary.pending_asks.append(line[:200])
            else:
                summary.key_facts.append(line[:200])
        
        # Limit items per category
        summary.resolved_tasks = summary.resolved_tasks[:5]
        summary.in_progress_state = summary.in_progress_state[:5]
        summary.pending_asks = summary.pending_asks[:5]
        summary.key_facts = summary.key_facts[:10]
        
        return summary
    
    def create_boundary_message(self, summary: StructuredSummary) -> str:
        """Create a boundary message to inject after compression."""
        return COMPACTION_BOUNDARY_MESSAGE.format(summary=summary.to_text())
    
    def compress_messages(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
        preserve_recent: int = 10,
        trigger: CompressionTrigger = CompressionTrigger.PROACTIVE,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Compress messages with structured summary.
        
        Args:
            messages: Messages to compress
            session_id: Session identifier
            preserve_recent: Number of recent messages to preserve
            trigger: What triggered this compression
            
        Returns:
            Tuple of (compressed_messages, boundary_message_or_none)
        """
        state = self._get_state(session_id)
        
        # Check circuit breaker
        if not state.can_compress():
            if self.debug:
                logger.warning(
                    f"[ProgressiveCompressor] Compression blocked for session {session_id}: "
                    f"attempts={state.attempts_this_turn}, failures={state.consecutive_failures}"
                )
            return messages, None
        
        # Find split point (protect head and tail)
        if len(messages) <= preserve_recent + 2:
            # Not enough messages to compress
            state.record_attempt(success=True, tokens_saved=0)
            return messages, None
        
        # Split: head (system) + middle (compress) + tail (preserve)
        split_index = len(messages) - preserve_recent
        
        # Find a good split point (at a user message boundary)
        for i in range(split_index, 0, -1):
            if messages[i].get("role") == "user":
                split_index = i
                break
        
        head = messages[:1]  # System message
        middle = messages[1:split_index]
        tail = messages[split_index:]
        
        if not middle:
            state.record_attempt(success=True, tokens_saved=0)
            return messages, None
        
        # Create structured summary from middle messages
        conversation_text = self._format_messages_for_summary(middle)
        summary = self.create_structured_summary(conversation_text)
        
        # Create compressed messages
        compressed_messages = head.copy()
        
        # Add summary as a system message
        summary_text = summary.to_text()
        if summary_text:
            compressed_messages.append({
                "role": "system",
                "content": f"[CONTEXT COMPACTION]\n\n{summary_text}"
            })
        
        # Add tail (recent messages)
        compressed_messages.extend(tail)
        
        # Create boundary message
        boundary_message = self.create_boundary_message(summary)
        
        # Record success
        # Estimate tokens saved using centralized estimator
        from logicore.runtime.context.token_estimator import TokenEstimator
        _estimator = TokenEstimator()
        middle_tokens = _estimator.count_messages_tokens(middle)
        state.record_attempt(success=True, tokens_saved=middle_tokens)
        
        if self.debug:
            logger.debug(
                f"[ProgressiveCompressor] Compressed {len(middle)} messages "
                f"(~{middle_tokens} tokens saved) for session {session_id}"
            )
        
        return compressed_messages, boundary_message
    
    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Format messages into text for summarization."""
        parts = []
        
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                texts = []
                for part in content:
                    if isinstance(part, dict):
                        if "text" in part:
                            texts.append(part["text"])
                        elif "functionResponse" in part:
                            texts.append(f"[Function result: {part['functionResponse'].get('name', 'unknown')}]")
                    elif isinstance(part, str):
                        texts.append(part)
                text = " ".join(texts)
            else:
                text = str(content)
            
            # Truncate very long content
            if len(text) > 1000:
                text = text[:1000] + "..."
            
            # Note tool calls
            if "tool_calls" in msg:
                tool_names = []
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict):
                        name = tc.get("function", {}).get("name", "unknown")
                    else:
                        name = getattr(getattr(tc, "function", None), "name", "unknown")
                    tool_names.append(name)
                text += f"\n[Called tools: {', '.join(tool_names)}]"
            
            parts.append(f"{role}: {text}")
        
        return "\n\n".join(parts)
    
    def get_compression_stats(self, session_id: str) -> Dict[str, Any]:
        """Get compression statistics for a session."""
        state = self._get_state(session_id)
        
        return {
            "session_id": session_id,
            "attempts_this_turn": state.attempts_this_turn,
            "max_attempts_per_turn": state.max_attempts_per_turn,
            "total_compressions": state.total_compressions,
            "total_tokens_saved": state.total_tokens_saved,
            "consecutive_failures": state.consecutive_failures,
            "last_compression_time": state.last_compression_time.isoformat() if state.last_compression_time else None,
        }
