"""
ContextWindowManager: Orchestrates context budget and compression.

Combines:
- TokenBudget: Track usage across categories
- CompressionService: Summarize old messages
- ToolOutputMaskingService: Mask bulky tool outputs

Key improvements over original context_middleware.py:
1. Model-specific context window awareness
2. Multi-stage compression (masking first, then summarization)
3. Async-safe processing
4. Comprehensive telemetry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable
import logging

from logicore.runtime.config import RuntimeConfig
from logicore.runtime.context.token_budget import TokenBudget, TokenCategory
from logicore.runtime.context.compression import CompressionService, CompressionResult, CompressionStatus
from logicore.runtime.context.masking import ToolOutputMaskingService, MaskingResult

logger = logging.getLogger(__name__)


@dataclass
class ContextManagementResult:
    """Result of context management operation."""
    original_tokens: int = 0
    final_tokens: int = 0
    tokens_saved: int = 0
    
    # Sub-operation results
    masking_result: Optional[MaskingResult] = None
    compression_result: Optional[CompressionResult] = None
    
    # Actions taken
    masked: bool = False
    compressed: bool = False
    truncated: bool = False
    
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def any_action_taken(self) -> bool:
        """Check if any context management action was taken."""
        return self.masked or self.compressed or self.truncated
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "tokens_saved": self.tokens_saved,
            "masked": self.masked,
            "compressed": self.compressed,
            "truncated": self.truncated,
            "masking_result": self.masking_result.to_dict() if self.masking_result else None,
            "compression_result": self.compression_result.to_dict() if self.compression_result else None,
            "timestamp": self.timestamp.isoformat(),
        }


class ContextWindowManager:
    """
    Manages context window budget through compression and masking.
    
    Strategy:
    1. Track token usage via TokenBudget
    2. When threshold approached, first try tool output masking
    3. If still over threshold, compress older messages
    4. As last resort, truncate oldest messages
    
    Usage:
        manager = ContextWindowManager(config, llm_provider, model_name="gpt-4")
        
        # Manage context before LLM call
        result, managed_messages = await manager.manage(messages, session_id)
        
        if result.any_action_taken:
            print(f"Saved {result.tokens_saved} tokens")
        
        # Use managed_messages for LLM call
    """
    
    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[Any] = None,
        model_name: str = "default",
        token_counter: Optional[Callable[[str], int]] = None,
    ):
        """
        Args:
            config: Runtime configuration
            llm_provider: LLM for compression (also used for context_window override)
            model_name: Model name for context window lookup
            token_counter: Optional custom token counter
        """
        self.config = config
        self.model_name = model_name
        from logicore.context_engine.token_estimator import TokenEstimator
        self._estimator = token_counter if isinstance(token_counter, TokenEstimator) else TokenEstimator(token_counter)
        self._token_counter = self._estimator.count_tokens
        
        # Initialize sub-services (pass llm_provider for context_window override)
        self.budget = TokenBudget(config, model_name, token_counter, provider=llm_provider)
        self.compression_service = CompressionService(config, llm_provider, token_counter)
        self.masking_service = ToolOutputMaskingService(config, token_counter)
        
        # Results history per session
        self._results: Dict[str, List[ContextManagementResult]] = {}
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._token_counter(text)
    
    def _estimate_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens in messages."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._count_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += self._count_tokens(part["text"])
                    elif isinstance(part, str):
                        total += self._count_tokens(part)
            
            # Tool calls overhead
            if "tool_calls" in msg:
                total += self._count_tokens(str(msg["tool_calls"])) // 2
        
        return total
    
    def _categorize_tokens(self, messages: List[Dict[str, Any]]) -> Dict[TokenCategory, int]:
        """Categorize tokens by type."""
        categories = {cat: 0 for cat in TokenCategory}
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if isinstance(content, str):
                tokens = self._count_tokens(content)
            else:
                tokens = self._count_tokens(str(content))
            
            if role == "system":
                categories[TokenCategory.SYSTEM] += tokens
            elif role == "tool":
                categories[TokenCategory.TOOL_RESULTS] += tokens
            else:
                categories[TokenCategory.MESSAGES] += tokens
            
            # Tool definitions (if embedded)
            if "tools" in msg:
                categories[TokenCategory.TOOLS] += self._count_tokens(str(msg["tools"])) // 2
        
        return categories
    
    async def manage(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> tuple[ContextManagementResult, List[Dict[str, Any]]]:
        """
        Manage context window for messages.
        
        Applies masking and compression as needed to keep within budget.
        
        Args:
            messages: Message history
            session_id: Session identifier
        
        Returns:
            Tuple of (ContextManagementResult, managed messages)
        """
        result = ContextManagementResult()
        current_messages = messages.copy()
        
        # Initial token count
        result.original_tokens = self._estimate_messages_tokens(current_messages)
        
        # Update budget tracking
        categories = self._categorize_tokens(current_messages)
        for category, tokens in categories.items():
            self.budget.set_tokens(category, tokens)
        
        # Check if management needed
        if not self.budget.should_compress():
            result.final_tokens = result.original_tokens
            return result, current_messages
        
        logger.info(
            f"[ContextWindowManager] Token budget exceeded: "
            f"{result.original_tokens}/{self.budget.compression_threshold} tokens. "
            f"Starting context management pipeline..."
        )
        print(
            f"\n[ContextWindowManager] ⚠️ Token budget exceeded: "
            f"{result.original_tokens}/{self.budget.compression_threshold} tokens. "
            f"Starting context management..."
        )
        
        # Stage 1: Tool output masking (fast, doesn't need LLM)
        if self.budget.should_mask_tool_outputs():
            masking_result, current_messages = self.masking_service.mask(
                current_messages,
                session_id,
            )
            
            if masking_result.masked_count > 0:
                result.masked = True
                result.masking_result = masking_result
                
                # Recount tokens
                new_tokens = self._estimate_messages_tokens(current_messages)
                result.tokens_saved += result.original_tokens - new_tokens
                
                # Update budget
                categories = self._categorize_tokens(current_messages)
                for category, tokens in categories.items():
                    self.budget.set_tokens(category, tokens)
        
        # Check if still need compression
        if not self.budget.should_compress():
            result.final_tokens = self._estimate_messages_tokens(current_messages)
            self._store_result(session_id, result)
            return result, current_messages
        
        # Stage 2: Compression (needs LLM)
        print("[ContextWindowManager] 🔄 Running context compression (this may take a moment)...")
        compression_result = await self.compression_service.compress(
            current_messages,
            session_id,
        )
        
        if compression_result.status == CompressionStatus.SUCCESS:
            current_messages = self.compression_service.build_compressed_messages(
                current_messages,
                compression_result,
            )
            result.compressed = True
            result.compression_result = compression_result
            result.tokens_saved += compression_result.tokens_saved
            print(
                f"[ContextWindowManager] ✅ Compression complete: "
                f"{result.original_tokens} → {self._estimate_messages_tokens(current_messages)} tokens"
            )
        else:
            print(
                f"[ContextWindowManager] ⚠️ Compression {compression_result.status.value}: "
                f"{compression_result.error or 'unknown reason'}"
            )
        
        # Stage 3: Emergency truncation if still over budget
        current_tokens = self._estimate_messages_tokens(current_messages)
        target_tokens = self.budget.get_compression_target()
        
        if current_tokens > target_tokens * 1.5:
            # Too large even after compression - truncate
            current_messages = self._truncate_messages(
                current_messages,
                target_tokens,
            )
            result.truncated = True
        
        # Final count
        result.final_tokens = self._estimate_messages_tokens(current_messages)
        result.tokens_saved = result.original_tokens - result.final_tokens
        
        # Record snapshot for tracking
        self.budget.record_snapshot()
        self._store_result(session_id, result)
        
        return result, current_messages
    
    def _truncate_messages(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int,
    ) -> List[Dict[str, Any]]:
        """
        Emergency truncation when compression isn't enough.
        
        Preserves:
        - System message
        - Most recent messages up to target
        """
        # Find system message
        system_msg = None
        start_idx = 0
        if messages and messages[0].get("role") == "system":
            system_msg = messages[0]
            start_idx = 1
        
        # Build from end until target reached
        result = []
        current_tokens = 0
        
        if system_msg:
            system_tokens = self._count_tokens(system_msg.get("content", ""))
            current_tokens += system_tokens
            result.append(system_msg)
        
        # Add messages from end
        for i in range(len(messages) - 1, start_idx - 1, -1):
            msg = messages[i]
            msg_tokens = self._count_tokens(str(msg.get("content", "")))
            
            if current_tokens + msg_tokens > target_tokens:
                break
            
            result.insert(1 if system_msg else 0, msg)
            current_tokens += msg_tokens
        
        return result
    
    def _store_result(self, session_id: str, result: ContextManagementResult) -> None:
        """Store result in history."""
        if session_id not in self._results:
            self._results[session_id] = []
        
        self._results[session_id].append(result)
        
        # Keep last 50 results
        if len(self._results[session_id]) > 50:
            self._results[session_id] = self._results[session_id][-50:]
    
    def get_management_history(self, session_id: str) -> List[ContextManagementResult]:
        """Get context management history for session."""
        return self._results.get(session_id, [])
    
    def get_budget_status(self) -> Dict[str, Any]:
        """Get current budget status."""
        return self.budget.to_dict()
    
    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        self._results.pop(session_id, None)
        self.compression_service.clear_session(session_id)
        self.budget.reset()
