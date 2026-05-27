"""
ToolOutputMaskingService: Backward-scanned FIFO masking for tool outputs.

Implements a "Hybrid Backward Scanned FIFO" algorithm:
1. Protection Window: Protects newest N tokens from pruning
2. Global Aggregation: Scans backwards to find prunable outputs
3. Batch Trigger: Only masks when prunable tokens exceed threshold

Inspired by gemini-cli's toolOutputMaskingService.
"""

from __future__ import annotations

import os
import tempfile
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable, Set

from logicore.runtime.config import RuntimeConfig


# Tools whose outputs should never be masked
EXEMPT_TOOLS: Set[str] = {
    "ask_user",
    "human_feedback", 
    "get_user_input",
    "confirm",
}

MASKING_INDICATOR = "[OUTPUT_MASKED]"


@dataclass
class MaskingResult:
    """Result of a masking operation."""
    masked_count: int = 0
    tokens_saved: int = 0
    masked_tool_names: List[str] = field(default_factory=list)
    output_files: List[str] = field(default_factory=list)  # Paths where masked content saved
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "masked_count": self.masked_count,
            "tokens_saved": self.tokens_saved,
            "masked_tool_names": self.masked_tool_names,
            "output_files": self.output_files,
            "timestamp": self.timestamp.isoformat(),
        }


class ToolOutputMaskingService:
    """
    Service for masking large tool outputs to preserve context window.
    
    Algorithm:
    1. Scan backwards from most recent message
    2. Protect recent tool outputs (protection_threshold_tokens)
    3. Identify prunable outputs past the protection window
    4. Mask only when total prunable exceeds min_prunable_tokens
    5. Save masked content to temp files for reference
    
    Usage:
        service = ToolOutputMaskingService(config)
        
        result, new_messages = service.mask(messages)
        
        if result.masked_count > 0:
            print(f"Masked {result.masked_count} outputs, saved {result.tokens_saved} tokens")
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        token_counter: Optional[Callable[[str], int]] = None,
        temp_dir: Optional[str] = None,
    ):
        """
        Args:
            config: Runtime configuration
            token_counter: Optional custom token counter
            temp_dir: Directory for saving masked outputs
        """
        self.config = config
        self._token_counter = token_counter or (lambda x: len(x) // 4)
        self.temp_dir = temp_dir or tempfile.gettempdir()
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._token_counter(text)
    
    def _is_tool_message(self, msg: Dict[str, Any]) -> bool:
        """Check if message is a tool result."""
        return msg.get("role") == "tool"
    
    def _get_tool_content(self, msg: Dict[str, Any]) -> Optional[str]:
        """Extract tool output content from message."""
        if not self._is_tool_message(msg):
            return None
        
        content = msg.get("content")
        
        if isinstance(content, str):
            return content
        
        if isinstance(content, dict):
            # Structured response
            return json.dumps(content)
        
        if isinstance(content, list):
            # Multi-part content
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if "text" in part:
                        parts.append(part["text"])
                    elif "functionResponse" in part:
                        response = part["functionResponse"].get("response", {})
                        parts.append(json.dumps(response))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(parts)
        
        return str(content) if content else None
    
    def _get_tool_name(self, msg: Dict[str, Any]) -> Optional[str]:
        """Get tool name from message."""
        return msg.get("name") or msg.get("tool_call_id")
    
    def _is_already_masked(self, content: str) -> bool:
        """Check if content is already masked."""
        return MASKING_INDICATOR in content
    
    def _is_exempt_tool(self, tool_name: Optional[str]) -> bool:
        """Check if tool is exempt from masking."""
        if not tool_name:
            return False
        return tool_name.lower() in {t.lower() for t in EXEMPT_TOOLS}
    
    def _save_masked_content(
        self,
        content: str,
        tool_name: str,
        session_id: str,
    ) -> str:
        """Save masked content to file and return path."""
        # Create session directory
        session_dir = os.path.join(self.temp_dir, "logicore_masked", session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{tool_name}_{timestamp}.txt"
        filepath = os.path.join(session_dir, filename)
        
        # Save content
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        
        return filepath
    
    def _create_masked_content(
        self,
        original_content: str,
        tool_name: str,
        filepath: str,
    ) -> str:
        """Create masked placeholder content."""
        line_count = original_content.count("\n") + 1
        char_count = len(original_content)
        token_estimate = self._count_tokens(original_content)
        
        return (
            f"{MASKING_INDICATOR}\n"
            f"Tool: {tool_name}\n"
            f"Output size: {char_count} chars (~{token_estimate} tokens), {line_count} lines\n"
            f"Full output saved to: {filepath}\n"
            f"Reason: Output masked to preserve context window"
        )
    
    def mask(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> tuple[MaskingResult, List[Dict[str, Any]]]:
        """
        Mask large tool outputs in message history.
        
        Args:
            messages: Message history
            session_id: Session identifier (for file paths)
        
        Returns:
            Tuple of (MaskingResult, masked messages)
        """
        if not messages:
            return MaskingResult(), messages
        
        config = self.config.context
        
        # Phase 1: Backward scan to identify prunable outputs
        cumulative_tokens = 0
        protection_reached = False
        prunable: List[tuple[int, int, str, str]] = []  # (msg_idx, tokens, content, tool_name)
        
        # Determine scan start (optionally skip latest turn)
        scan_start = len(messages) - 1
        if config.protect_latest_turn and scan_start > 0:
            scan_start -= 1
        
        for i in range(scan_start, -1, -1):
            msg = messages[i]
            
            if not self._is_tool_message(msg):
                continue
            
            tool_name = self._get_tool_name(msg) or "unknown"
            
            # Skip exempt tools
            if self._is_exempt_tool(tool_name):
                continue
            
            content = self._get_tool_content(msg)
            
            if not content or self._is_already_masked(content):
                continue
            
            tokens = self._count_tokens(content)
            
            if not protection_reached:
                cumulative_tokens += tokens
                if cumulative_tokens > config.protection_threshold_tokens:
                    protection_reached = True
                    # This message crossed the boundary - it's prunable
                    prunable.append((i, tokens, content, tool_name))
            else:
                # Past protection window - all are prunable
                prunable.append((i, tokens, content, tool_name))
        
        # Phase 2: Check if we have enough to justify masking
        total_prunable_tokens = sum(tokens for _, tokens, _, _ in prunable)
        
        if total_prunable_tokens < config.min_prunable_tokens:
            return MaskingResult(), messages
        
        # Phase 3: Apply masking
        result = MaskingResult()
        new_messages = messages.copy()
        
        for msg_idx, tokens, content, tool_name in prunable:
            # Save original content
            filepath = self._save_masked_content(content, tool_name, session_id)
            
            # Create masked version
            masked_content = self._create_masked_content(content, tool_name, filepath)
            
            # Update message
            new_messages[msg_idx] = {
                **new_messages[msg_idx],
                "content": masked_content,
            }
            
            # Track results
            result.masked_count += 1
            result.tokens_saved += tokens - self._count_tokens(masked_content)
            result.masked_tool_names.append(tool_name)
            result.output_files.append(filepath)
        
        return result, new_messages
    
    def add_exempt_tool(self, tool_name: str) -> None:
        """Add a tool to the exempt list."""
        EXEMPT_TOOLS.add(tool_name)
    
    def remove_exempt_tool(self, tool_name: str) -> None:
        """Remove a tool from the exempt list."""
        EXEMPT_TOOLS.discard(tool_name)
