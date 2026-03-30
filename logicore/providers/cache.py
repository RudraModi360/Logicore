import json
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class CapabilityCache:
    """
    Caches model capabilities to avoid redundant API calls or probing.
    Supports Redis for persistence and in-memory as a fallback.
    """
    
    def __init__(self, use_redis: bool = None):
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self.redis_client = None
        
        # Use environment variable if use_redis not explicitly set
        if use_redis is None:
            use_redis = os.getenv("LOGICORE_USE_REDIS", "false").lower() == "true"
        
        if use_redis:
            try:
                import redis
                self.redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
                # Test connection
                self.redis_client.ping()
                logger.info("[CapabilityCache] Connected to Redis.")
            except Exception as e:
                logger.warning(f"[CapabilityCache] Redis connection failed, falling back to in-memory: {e}")
                self.redis_client = None

    def _get_key(self, provider: str, model: str) -> str:
        return f"logicore:caps:{provider}:{model}"

    def get(self, provider: str, model: str) -> Optional[Dict[str, Any]]:
        key = self._get_key(provider, model)
        
        # Try Redis
        if self.redis_client:
            try:
                data = self.redis_client.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.error(f"[CapabilityCache] Redis GET failed: {e}")
        
        # Fallback to memory
        return self._memory_cache.get(key)

    def set(self, provider: str, model: str, capabilities: Dict[str, Any]):
        key = self._get_key(provider, model)
        
        # Save to memory anyway
        self._memory_cache[key] = capabilities
        
        # Save to Redis
        if self.redis_client:
            try:
                self.redis_client.set(key, json.dumps(capabilities), ex=86400 * 7) # Cache for 7 days
            except Exception as e:
                logger.error(f"[CapabilityCache] Redis SET failed: {e}")

# Global instance
_capability_cache = None

def get_capability_cache() -> CapabilityCache:
    global _capability_cache
    if _capability_cache is None:
        _capability_cache = CapabilityCache()
    return _capability_cache
