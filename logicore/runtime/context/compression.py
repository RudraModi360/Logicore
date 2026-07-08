"""
CompressionService: Intelligent history summarization.

Key improvements over original context_middleware.py:
1. Runs OUTSIDE the main agent loop (no nested LLM risk)
2. Uses async queue for background processing
3. Garbage collects old summaries
4. Tracks compression history for debugging
5. Configurable via RuntimeConfig

Inspired by gemini-cli's chatCompressionService.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Callable

from logicore.runtime.config import RuntimeConfig

logger = logging.getLogger(__name__)


class CompressionStatus(Enum):
    """Status of a compression operation."""
    SUCCESS = "success"
    FAILED_INFLATED = "failed_inflated"  # Summary larger than original
    FAILED_EMPTY = "failed_empty"        # Empty summary
    FAILED_ERROR = "failed_error"        # LLM error
    SKIPPED_SMALL = "skipped_small"      # Below threshold
    PENDING = "pending"


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    status: CompressionStatus
    original_tokens: int = 0
    compressed_tokens: int = 0
    messages_compressed: int = 0
    messages_preserved: int = 0
    summary: Optional[str] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def compression_ratio(self) -> float:
        """Get compression ratio (0.0-1.0, lower is better)."""
        if self.original_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.original_tokens
    
    @property
    def tokens_saved(self) -> int:
        """Get number of tokens saved."""
        return max(0, self.original_tokens - self.compressed_tokens)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "status": self.status.value,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": self.compression_ratio,
            "tokens_saved": self.tokens_saved,
            "messages_compressed": self.messages_compressed,
            "messages_preserved": self.messages_preserved,
            "timestamp": self.timestamp.isoformat(),
            "error": self.error,
        }


# Type for LLM provider
LLMProvider = Any  # To avoid circular imports


class CompressionService:
    """
    Service for compressing conversation history.
    
    Key design decisions:
    1. Async operation - doesn't block the main loop
    2. Queued processing - can batch compressions
    3. Summary garbage collection - removes stale summaries
    4. Split point detection - respects conversation boundaries
    
    Usage:
        service = CompressionService(config, llm_provider)
        
        # Check if compression needed
        if budget.should_compress():
            # Queue compression (non-blocking)
            await service.queue_compression(messages, session_id)
        
        # Later, get compressed messages
        compressed = await service.get_compressed_messages(session_id)
    """
    
    COMPRESSION_PROMPT = """You are a context manager. Summarize the following conversation segment, preserving:
1. Key facts and decisions
2. Important context for continuing the conversation
3. Current state and progress
4. Any pending tasks or questions

Be concise but complete. Focus on what the AI needs to know to continue effectively.

CONVERSATION SEGMENT:
{conversation}

SUMMARY:"""
    
    def __init__(
        self,
        config: RuntimeConfig,
        llm_provider: Optional[LLMProvider] = None,
        token_counter: Optional[Callable[[str], int]] = None,
    ):
        """
        Args:
            config: Runtime configuration
            llm_provider: LLM for generating summaries
            token_counter: Optional custom token counter
        """
        self.config = config
        self.llm = llm_provider
        from logicore.context_engine.token_estimator import TokenEstimator
        self._estimator = token_counter if isinstance(token_counter, TokenEstimator) else TokenEstimator(token_counter)
        self._token_counter = self._estimator.count_tokens
        
        # Compression state per session
        self._pending: Dict[str, List[Dict[str, Any]]] = {}  # session_id -> messages
        self._summaries: Dict[str, List[str]] = {}  # session_id -> list of summaries
        self._results: Dict[str, List[CompressionResult]] = {}  # session_id -> results
        
        # Processing queue
        self._queue: asyncio.Queue = asyncio.Queue()
        self._processing = False
        self._processor_task: Optional[asyncio.Task] = None
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._token_counter(text)
    
    def _estimate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate tokens in a list of messages."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._count_tokens(content)
            elif isinstance(content, list):
                # Handle multimodal content
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += self._count_tokens(part["text"])
            
            # Tool calls add some overhead
            if "tool_calls" in msg:
                total += self._count_tokens(str(msg["tool_calls"])) // 2
        
        return total
    
    def _find_split_point(
        self,
        messages: List[Dict[str, Any]],
        preserve_count: int,
    ) -> int:
        """
        Find the index to split messages for compression.
        
        Returns index of first message to preserve (everything before is compressed).
        Respects conversation boundaries (splits at user messages).
        """
        if len(messages) <= preserve_count + 2:
            return 0  # Not enough to compress
        
        # Target: everything except last preserve_count messages
        target_index = len(messages) - preserve_count
        
        # Find nearest user message boundary (going backward from target)
        for i in range(target_index, 0, -1):
            msg = messages[i]
            role = msg.get("role", "")
            
            # Good split points: user messages that aren't function responses
            if role == "user":
                parts = msg.get("parts", msg.get("content", []))
                if isinstance(parts, list):
                    has_function_response = any(
                        isinstance(p, dict) and "functionResponse" in p
                        for p in parts
                    )
                else:
                    has_function_response = False
                
                if not has_function_response:
                    return i
        
        # Fallback to target
        return target_index
    
    def _format_messages_for_summary(
        self,
        messages: List[Dict[str, Any]],
    ) -> str:
        """Format messages into text for summarization."""
        parts = []
        
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # Extract text from multimodal content
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
    
    async def compress(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> CompressionResult:
        """
        Compress messages synchronously.
        
        This is the main compression method. For non-blocking operation,
        use queue_compression() instead.
        
        Args:
            messages: Messages to compress
            session_id: Session identifier
        
        Returns:
            CompressionResult with status and compressed messages
        """
        # Check if compression needed
        original_tokens = self._estimate_message_tokens(messages)
        threshold = self.config.context.compression_threshold_tokens
        
        if original_tokens < threshold:
            return CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=0,
                messages_preserved=len(messages),
            )
        
        # Find split point
        preserve_count = self.config.context.preserve_recent_count
        split_index = self._find_split_point(messages, preserve_count)
        
        if split_index == 0:
            return CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=0,
                messages_preserved=len(messages),
            )
        
        # Extract system message
        system_msg = None
        start_index = 0
        if messages and messages[0].get("role") == "system":
            system_msg = messages[0]
            start_index = 1
        
        # Adjust split for system message
        if start_index >= split_index:
            return CompressionResult(
                status=CompressionStatus.SKIPPED_SMALL,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=0,
                messages_preserved=len(messages),
            )
        
        # Messages to compress vs preserve
        to_compress = messages[start_index:split_index]
        to_preserve = messages[split_index:]
        
        # Generate summary
        if not self.llm:
            return CompressionResult(
                status=CompressionStatus.FAILED_ERROR,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                error="No LLM provider configured for compression",
            )
        
        try:
            conversation_text = self._format_messages_for_summary(to_compress)
            prompt = self.COMPRESSION_PROMPT.format(conversation=conversation_text)
            
            logger.info(
                f"[CompressionService] 🔄 Compressing {len(to_compress)} messages "
                f"({original_tokens} tokens → target {self.config.context.compression_threshold_tokens})"
            )
            print(
                f"\n[CompressionService] 🔄 Compressing {len(to_compress)} messages "
                f"({original_tokens} tokens)..."
            )
            
            # Use timeout to prevent indefinite blocking
            _COMPRESSION_TIMEOUT = 120  # seconds
            try:
                response = await asyncio.wait_for(
                    self.llm.chat([
                        {"role": "user", "content": prompt}
                    ], tools=None),
                    timeout=_COMPRESSION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[CompressionService] ⏰ Compression LLM call timed out after {_COMPRESSION_TIMEOUT}s"
                )
                print(
                    f"[CompressionService] ⏰ Compression timed out after {_COMPRESSION_TIMEOUT}s, "
                    f"skipping compression."
                )
                return CompressionResult(
                    status=CompressionStatus.FAILED_ERROR,
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    messages_compressed=len(to_compress),
                    messages_preserved=len(to_preserve),
                    error=f"Compression LLM call timed out after {_COMPRESSION_TIMEOUT}s",
                )
            
            # Extract summary text
            if isinstance(response, dict):
                summary = response.get("content", "")
            else:
                summary = getattr(response, "content", str(response))
            
            summary = summary.strip()
            
            if not summary:
                logger.warning("[CompressionService] ⚠️ Compression returned empty summary")
                print("[CompressionService] ⚠️ Compression returned empty summary")
                return CompressionResult(
                    status=CompressionStatus.FAILED_EMPTY,
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    messages_compressed=len(to_compress),
                    messages_preserved=len(to_preserve),
                    error="Summary was empty",
                )
            
            # Build compressed history
            summary_tokens = self._count_tokens(summary)
            compressed_tokens = summary_tokens + self._estimate_message_tokens(to_preserve)
            
            # Check if compression actually helped
            if compressed_tokens >= original_tokens:
                logger.warning(
                    f"[CompressionService] ⚠️ Compression inflated tokens: "
                    f"{original_tokens} → {compressed_tokens}"
                )
                print(
                    f"[CompressionService] ⚠️ Compression inflated tokens, skipping"
                )
                return CompressionResult(
                    status=CompressionStatus.FAILED_INFLATED,
                    original_tokens=original_tokens,
                    compressed_tokens=compressed_tokens,
                    messages_compressed=len(to_compress),
                    messages_preserved=len(to_preserve),
                    summary=summary,
                    error="Compression inflated token count",
                )
            
            # Store summary
            if session_id not in self._summaries:
                self._summaries[session_id] = []
            
            # Garbage collect old summaries (keep last 3)
            self._summaries[session_id].append(summary)
            
            logger.info(
                f"[CompressionService] ✅ Compression complete: "
                f"{original_tokens} → {compressed_tokens} tokens "
                f"({len(to_compress)} messages compressed, {len(to_preserve)} preserved)"
            )
            print(
                f"[CompressionService] ✅ Compression complete: "
                f"{original_tokens} → {compressed_tokens} tokens"
            )
            if len(self._summaries[session_id]) > 3:
                self._summaries[session_id] = self._summaries[session_id][-3:]
            
            return CompressionResult(
                status=CompressionStatus.SUCCESS,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                messages_compressed=len(to_compress),
                messages_preserved=len(to_preserve),
                summary=summary,
            )
            
        except Exception as e:
            logger.error(f"[CompressionService] ❌ Compression failed: {e}")
            print(f"[CompressionService] ❌ Compression failed: {e}")
            return CompressionResult(
                status=CompressionStatus.FAILED_ERROR,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                messages_compressed=len(to_compress),
                messages_preserved=len(to_preserve),
                error=str(e),
            )
    
    def build_compressed_messages(
        self,
        messages: List[Dict[str, Any]],
        result: CompressionResult,
    ) -> List[Dict[str, Any]]:
        """
        Build compressed message list from compression result.
        
        Args:
            messages: Original messages
            result: Compression result with summary
        
        Returns:
            Compressed message list
        """
        if result.status != CompressionStatus.SUCCESS or not result.summary:
            return messages
        
        # Extract system message
        system_msg = None
        start_index = 0
        if messages and messages[0].get("role") == "system":
            system_msg = messages[0]
            start_index = 1
        
        # Build new history
        new_messages = []
        
        if system_msg:
            new_messages.append(system_msg)
        
        # Add summary as system message
        new_messages.append({
            "role": "system",
            "content": f"=== Previous Conversation Summary ===\n{result.summary}\n====================================="
        })
        
        # Add preserved messages
        preserve_count = result.messages_preserved
        new_messages.extend(messages[-preserve_count:])
        
        return new_messages
    
    async def queue_compression(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = "default",
    ) -> None:
        """
        Queue messages for background compression.
        
        Non-blocking - returns immediately.
        """
        await self._queue.put((session_id, messages.copy()))
        
        # Start processor if not running
        if not self._processing:
            self._start_processor()
    
    def _start_processor(self) -> None:
        """Start background compression processor."""
        if self._processor_task and not self._processor_task.done():
            return
        
        self._processing = True
        self._processor_task = asyncio.create_task(self._process_queue())
    
    async def _process_queue(self) -> None:
        """Process compression queue."""
        try:
            while True:
                try:
                    session_id, messages = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=5.0,
                    )
                    
                    result = await self.compress(messages, session_id)
                    
                    # Store result
                    if session_id not in self._results:
                        self._results[session_id] = []
                    self._results[session_id].append(result)
                    
                    self._queue.task_done()
                    
                except asyncio.TimeoutError:
                    # No more items, stop processing
                    if self._queue.empty():
                        break
        finally:
            self._processing = False
    
    def get_compression_history(self, session_id: str) -> List[CompressionResult]:
        """Get compression history for a session."""
        return self._results.get(session_id, [])
    
    def get_summaries(self, session_id: str) -> List[str]:
        """Get accumulated summaries for a session."""
        return self._summaries.get(session_id, [])
    
    def clear_session(self, session_id: str) -> None:
        """Clear all state for a session."""
        self._pending.pop(session_id, None)
        self._summaries.pop(session_id, None)
        self._results.pop(session_id, None)
