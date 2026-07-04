"""
Persistent Tool Result Cache (Layer 1).

LRU cache with TTL that persists across chat() turns within a session.
Replaces the ephemeral per-chat() dict in agent/base.py.

Uses SHA-256 hashed signatures as keys.
"""

import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class CacheEntry:
    """A single cached tool result."""
    result: Dict[str, Any]
    content_hash: str
    timestamp: float
    call_count: int  # How many times this result was reused


class ResultCache:
    """
    Cross-turn persistent result cache for tool calls.

    - Key: SHA-256 hash of (tool_name + sorted_args)
    - Value: (result_dict, content_hash, timestamp, call_count)
    - Eviction: LRU with configurable max entries and TTL
    - Thread-safe via lock
    """

    def __init__(self, max_entries: int = 500, ttl_seconds: int = 600):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self.stats_hits = 0
        self.stats_misses = 0
        self.stats_evictions = 0

    def get(self, signature: str) -> Optional[Dict[str, Any]]:
        """
        Look up a cached result by signature.
        Returns the result dict if found and not expired.
        """
        with self._lock:
            entry = self._cache.get(signature)
            if entry is None:
                self.stats_misses += 1
                return None

            # Check TTL
            if time.time() - entry.timestamp > self._ttl_seconds:
                del self._cache[signature]
                self.stats_misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(signature)
            entry.call_count += 1
            self.stats_hits += 1
            return entry.result

    def set(self, signature: str, result: Dict[str, Any], content_hash: str = "") -> None:
        """Store a tool result in the cache."""
        if not result.get("success"):
            return  # Don't cache failed results

        with self._lock:
            # Remove old entry if exists
            if signature in self._cache:
                del self._cache[signature]

            # Evict LRU entries if over limit
            while len(self._cache) >= self._max_entries:
                if not self._cache:
                    break
                self._cache.popitem(last=False)
                self.stats_evictions += 1

            self._cache[signature] = CacheEntry(
                result=result,
                content_hash=content_hash,
                timestamp=time.time(),
                call_count=0,
            )

    def invalidate(self, signature: str) -> bool:
        """Remove a specific entry from the cache."""
        with self._lock:
            if signature in self._cache:
                del self._cache[signature]
                return True
            return False

    def clear(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self.stats_hits + self.stats_misses
        reuse_count = 0
        with self._lock:
            for entry in self._cache.values():
                reuse_count += entry.call_count
        return {
            "entries": self.size,
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl_seconds,
            "hits": self.stats_hits,
            "misses": self.stats_misses,
            "evictions": self.stats_evictions,
            "total_reuses": reuse_count,
            "hit_rate": round(self.stats_hits / total, 4) if total > 0 else 0.0,
        }


# Module-level singleton (per-process, shared across agents)
result_cache = ResultCache()
