"""
PromptCacheManager: Tracks cacheable message prefixes for LLM providers.

This module identifies which parts of the message history are "cacheable":
- System messages (constant across requests)
- Tool schemas (constant within a session)
- Early conversation messages (stable prefix)

Providers with native prefix caching (OpenAI, Groq, Anthropic) automatically
cache these prefixes on their servers. This module:
1. Identifies the cacheable prefix boundary
2. Provides cache control annotations for provider-specific APIs
3. Tracks cache statistics for telemetry

For Ollama (local inference):
- Models stay loaded in memory between requests (keep_alive)
- Context window persists as long as model is loaded
- No cloud-based caching, but inference is fast due to local execution
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime


@dataclass
class CacheStats:
    """Cache statistics for telemetry."""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    tokens_saved: int = 0
    estimated_cost_saved: float = 0.0
    
    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_rate": round(self.hit_rate, 3),
            "tokens_saved": self.tokens_saved,
            "estimated_cost_saved": round(self.estimated_cost_saved, 6),
        }


class PromptCacheManager:
    """
    Manages prompt caching for LLM providers that support automatic prefix caching.
    
    How provider-level prefix caching works:
    
    Request 1:
    [System msg] [Tools] [User msg 1] -> Provider caches [System + Tools]
    
    Request 2:
    [System msg] [Tools] [User msg 1] [Assistant 1] [Tool Result 1] [User msg 2]
    -> Provider recognizes prefix match, reuses cached [System + Tools]
    -> Only processes NEW tokens (User msg 2)
    
    Result: Lower latency, lower cost, same quality.
    
    This module:
    1. Identifies the cacheable prefix boundary (where system+tools end)
    2. Adds cache_control annotations for Anthropic-style APIs
    3. Tracks how many tokens are being cached vs reprocessed
    
    For Ollama (local inference):
    - No cloud caching, but model stays loaded in GPU memory
    - Context window persists between requests (if keep_alive > 0)
    - Optimization: Keep model loaded, reduce cold start latency
    """
    
    def __init__(
        self,
        enabled: bool = True,
        ttl_seconds: int = 300,
        max_entries: int = 100,
        debug: bool = False,
    ):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.debug = debug
        
        self._stats = CacheStats()
        self._current_system_hash: Optional[str] = None
        self._current_tools_hash: Optional[str] = None
        
    def _hash_content(self, content: Any) -> str:
        """Generate hash for content."""
        if isinstance(content, str):
            return hashlib.sha256(content.encode()).hexdigest()[:16]
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]
    
    def update_prefix_state(
        self,
        system_messages: List[Dict[str, Any]],
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Update the cacheable prefix state when system prompt or tools change."""
        if not self.enabled:
            return
        
        system_content = " ".join(
            str(m.get("content", "")) 
            for m in system_messages 
            if m.get("role") == "system"
        )
        new_system_hash = self._hash_content(system_content)
        new_tools_hash = self._hash_content(tool_schemas) if tool_schemas else "none"
        
        if (new_system_hash != self._current_system_hash or 
            new_tools_hash != self._current_tools_hash):
            
            if self.debug:
                print(f"[PromptCache] Prefix changed: system={new_system_hash[:8]}, tools={new_tools_hash[:8]}")
            
            self._current_system_hash = new_system_hash
            self._current_tools_hash = new_tools_hash
    
    def find_prefix_boundary(self, messages: List[Dict[str, Any]]) -> int:
        """Find where the cacheable prefix ends (first user message index)."""
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                return i
        return len(messages)
    
    def annotate_messages(
        self,
        messages: List[Dict[str, Any]],
        system_hash: Optional[str] = None,
        tools_hash: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Annotate messages with cache control metadata.
        
        For Anthropic: Adds cache_control breakpoints
        For OpenAI/Groq: No explicit control needed (automatic prefix caching)
        """
        if not self.enabled:
            return messages
        
        sys_hash = system_hash or self._current_system_hash
        tools_hash = tools_hash or self._current_tools_hash
        
        if not sys_hash:
            return messages
        
        annotated = []
        prefix_boundary = self.find_prefix_boundary(messages)
        
        for i, msg in enumerate(messages):
            annotated_msg = msg.copy()
            
            if i < prefix_boundary:
                role = msg.get("role", "")
                
                if role == "system":
                    annotated_msg["_cache_control"] = {
                        "type": "ephemeral",
                        "cacheable": True,
                        "position": "prefix",
                    }
                elif i == prefix_boundary - 1 and prefix_boundary > 0:
                    annotated_msg["_cache_control"] = {
                        "type": "breakpoint",
                        "cacheable": True,
                        "position": "prefix_end",
                    }
            
            annotated.append(annotated_msg)
        
        return annotated
    
    def record_request(
        self,
        tokens_saved: int = 0,
        cost_saved: float = 0.0,
        cache_hit: bool = False,
    ) -> None:
        """Record a request for telemetry."""
        if not self.enabled:
            return
        
        self._stats.total_requests += 1
        
        if cache_hit:
            self._stats.cache_hits += 1
            self._stats.tokens_saved += tokens_saved
            self._stats.estimated_cost_saved += cost_saved
        else:
            self._stats.cache_misses += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self._stats.to_dict()
    
    def clear_cache(self) -> None:
        """Clear all cache state."""
        self._current_system_hash = None
        self._current_tools_hash = None


_prompt_cache_manager: Optional[PromptCacheManager] = None


def get_prompt_cache_manager(
    enabled: bool = True,
    ttl_seconds: int = 300,
    max_entries: int = 100,
    debug: bool = False,
) -> PromptCacheManager:
    """Get or create the global PromptCacheManager instance."""
    global _prompt_cache_manager
    
    if _prompt_cache_manager is None:
        _prompt_cache_manager = PromptCacheManager(
            enabled=enabled,
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
            debug=debug,
        )
    
    return _prompt_cache_manager
