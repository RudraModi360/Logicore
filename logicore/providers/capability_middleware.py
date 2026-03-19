"""
Provider Capability Middleware

This middleware layer sits between the user's provider selection and the agent initialization.
It probes and validates LLM provider capabilities (tool calling, vision support, etc.)
and gates access to features based on what the model actually supports.

Usage:
    middleware = CapabilityMiddleware()
    result = await middleware.validate_provider(provider_name, model_name, provider_instance)
    
    if result.is_ready:
        # Provider is validated and capabilities are known
        agent = create_agent(provider_instance)
        if result.capabilities.supports_tools:
            agent.load_tools()
        if result.capabilities.supports_vision:
            agent.enable_vision()
"""

import asyncio
import time
from typing import Dict, Any, Optional, Tuple, Callable, Union
from dataclasses import dataclass, field
from enum import Enum

from logicore.providers.capability_detector import (
    CapabilityDetector,
    ModelCapabilities,
    detect_model_capabilities,
    get_known_capability,
    KNOWN_CAPABILITIES
)


class ValidationStatus(Enum):
    """Status of capability validation."""
    PENDING = "pending"          # Not yet validated
    VALIDATING = "validating"    # Currently being validated
    SUCCESS = "success"          # Successfully validated
    FAILED = "failed"           # Validation failed
    PARTIAL = "partial"          # Partial capabilities detected


@dataclass
class ValidationResult:
    """Result of capability validation."""
    status: ValidationStatus
    capabilities: Optional[ModelCapabilities] = None
    is_ready: bool = False
    error_message: Optional[str] = None
    validation_time_ms: float = 0
    warnings: list = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "is_ready": self.is_ready,
            "capabilities": self.capabilities.to_dict() if self.capabilities else None,
            "error_message": self.error_message,
            "validation_time_ms": self.validation_time_ms,
            "warnings": self.warnings
        }


class CapabilityMiddleware:
    """
    Middleware for validating and gating LLM provider capabilities.
    
    Features:
    - Validates capabilities when a provider is selected
    - Caches validation results for performance
    - Provides feature gating based on capabilities
    - Supports async probing for unknown models
    """
    
    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize the middleware.
        
        Args:
            cache_ttl_seconds: How long to cache validation results (default: 5 minutes)
        """
        self._cache: Dict[str, Tuple[ValidationResult, float]] = {}
        self._cache_ttl = cache_ttl_seconds
        self._validation_locks: Dict[str, asyncio.Lock] = {}
    
    def _get_cache_key(self, provider_name: str, model_name: str) -> str:
        """Generate a cache key for a provider/model combination."""
        return f"{provider_name.lower()}:{model_name.lower()}"
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached result is still valid."""
        if cache_key not in self._cache:
            return False
        _, cached_time = self._cache[cache_key]
        return (time.time() - cached_time) < self._cache_ttl
    
    async def validate_provider(
        self,
        provider_name: str,
        model_name: str,
        provider_instance=None,
        force_probe: bool = False
    ) -> ValidationResult:
        """
        Validate a provider's capabilities.
        
        This is the main entry point for the middleware. It will:
        1. Check the cache for existing validation results
        2. Try fast lookup from known capabilities
        3. Fall back to probing the model if needed
        
        Args:
            provider_name: Name of the provider (e.g., 'ollama', 'groq', 'gemini')
            model_name: Model identifier
            provider_instance: Optional provider instance for live probing
            force_probe: If True, skip cache and probe the model
            
        Returns:
            ValidationResult with capabilities and status
        """
        start_time = time.time()
        cache_key = self._get_cache_key(provider_name, model_name)
        
        # Check cache first (unless force_probe is True)
        if not force_probe and self._is_cache_valid(cache_key):
            result, _ = self._cache[cache_key]
            return result
        
        # Get or create lock for this provider/model combo
        if cache_key not in self._validation_locks:
            self._validation_locks[cache_key] = asyncio.Lock()
        
        async with self._validation_locks[cache_key]:
            # Double-check cache after acquiring lock
            if not force_probe and self._is_cache_valid(cache_key):
                result, _ = self._cache[cache_key]
                return result
            
            try:
                # Attempt capability detection
                capabilities = await detect_model_capabilities(
                    provider_name=provider_name,
                    model_name=model_name,
                    provider_instance=provider_instance
                )
                
                # Build warnings list
                warnings = []
                if capabilities.detection_method == "default":
                    warnings.append(f"Capabilities for '{model_name}' were not found in known models. Using conservative defaults.")
                elif capabilities.detection_method == "probe":
                    warnings.append(f"Capabilities for '{model_name}' were detected via live probe. Results may vary.")
                
                if capabilities.error_message:
                    warnings.append(capabilities.error_message)
                
                if not capabilities.supports_tools:
                    warnings.append("This model does not support tool calling. Only basic chat is available.")
                
                validation_time = (time.time() - start_time) * 1000
                
                result = ValidationResult(
                    status=ValidationStatus.SUCCESS,
                    capabilities=capabilities,
                    is_ready=True,
                    validation_time_ms=validation_time,
                    warnings=warnings
                )
                
                # Cache the result
                self._cache[cache_key] = (result, time.time())
                
                return result
                
            except Exception as e:
                validation_time = (time.time() - start_time) * 1000
                return ValidationResult(
                    status=ValidationStatus.FAILED,
                    is_ready=False,
                    error_message=str(e),
                    validation_time_ms=validation_time
                )
    
    def get_quick_capabilities(self, model_name: str) -> Optional[Dict[str, bool]]:
        """
        Get capabilities from known models (synchronous, fast).
        
        Use this for quick lookups in UI before full validation.
        
        Args:
            model_name: Model identifier
            
        Returns:
            Dict with supports_tools and supports_vision, or None if unknown
        """
        return get_known_capability(model_name)
    
    def is_model_known(self, model_name: str) -> bool:
        """
        Check if a model is in the known capabilities list.
        
        Args:
            model_name: Model identifier
            
        Returns:
            True if model is known, False otherwise
        """
        return get_known_capability(model_name) is not None
    
    def invalidate_cache(self, provider_name: str = None, model_name: str = None):
        """
        Invalidate cached validation results.
        
        Args:
            provider_name: If provided with model_name, invalidate specific entry
            model_name: If provided with provider_name, invalidate specific entry
            
        If both are None, clears entire cache.
        """
        if provider_name and model_name:
            cache_key = self._get_cache_key(provider_name, model_name)
            self._cache.pop(cache_key, None)
        else:
            self._cache.clear()
    
    def get_cached_capabilities(self, provider_name: str, model_name: str) -> Optional[ModelCapabilities]:
        """
        Get cached capabilities without triggering validation.
        
        Args:
            provider_name: Provider name
            model_name: Model name
            
        Returns:
            Cached ModelCapabilities or None if not cached
        """
        cache_key = self._get_cache_key(provider_name, model_name)
        if self._is_cache_valid(cache_key):
            result, _ = self._cache[cache_key]
            return result.capabilities
        return None


class FeatureGate:
    """
    Feature gating based on model capabilities.
    
    This class provides decorators and utilities for gating features
    based on what the current model supports.
    """
    
    def __init__(self, capabilities: ModelCapabilities = None):
        """
        Initialize feature gate with model capabilities.
        
        Args:
            capabilities: ModelCapabilities object
        """
        self._capabilities = capabilities
    
    def update_capabilities(self, capabilities: ModelCapabilities):
        """Update the capabilities for this gate."""
        self._capabilities = capabilities
    
    @property
    def supports_tools(self) -> bool:
        """Check if current model supports tool calling."""
        return self._capabilities.supports_tools if self._capabilities else False
    
    @property
    def supports_vision(self) -> bool:
        """Check if current model supports vision/image input."""
        return self._capabilities.supports_vision if self._capabilities else False
    
    @property
    def supports_streaming(self) -> bool:
        """Check if current model supports streaming responses."""
        return self._capabilities.supports_streaming if self._capabilities else True
    
    @property
    def supports_json_mode(self) -> bool:
        """Check if current model supports JSON output mode."""
        return self._capabilities.supports_json_mode if self._capabilities else False
    
    def require_tools(self, fallback: Any = None) -> Callable:
        """
        Decorator to require tool support for a function.
        
        Args:
            fallback: Value to return if tools not supported (default: raise error)
            
        Usage:
            @feature_gate.require_tools(fallback="Tools not supported")
            async def use_tool(tool_name, args):
                ...
        """
        def decorator(func):
            async def async_wrapper(*args, **kwargs):
                if not self.supports_tools:
                    if fallback is not None:
                        return fallback
                    raise RuntimeError(
                        f"Model '{self._capabilities.model_name if self._capabilities else 'unknown'}' "
                        "does not support tool calling"
                    )
                return await func(*args, **kwargs)
            
            def sync_wrapper(*args, **kwargs):
                if not self.supports_tools:
                    if fallback is not None:
                        return fallback
                    raise RuntimeError(
                        f"Model '{self._capabilities.model_name if self._capabilities else 'unknown'}' "
                        "does not support tool calling"
                    )
                return func(*args, **kwargs)
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        return decorator
    
    def require_vision(self, fallback: Any = None) -> Callable:
        """
        Decorator to require vision support for a function.
        
        Args:
            fallback: Value to return if vision not supported
            
        Usage:
            @feature_gate.require_vision(fallback="Vision not supported")
            async def analyze_image(image_data):
                ...
        """
        def decorator(func):
            async def async_wrapper(*args, **kwargs):
                if not self.supports_vision:
                    if fallback is not None:
                        return fallback
                    raise RuntimeError(
                        f"Model '{self._capabilities.model_name if self._capabilities else 'unknown'}' "
                        "does not support vision/image input"
                    )
                return await func(*args, **kwargs)
            
            def sync_wrapper(*args, **kwargs):
                if not self.supports_vision:
                    if fallback is not None:
                        return fallback
                    raise RuntimeError(
                        f"Model '{self._capabilities.model_name if self._capabilities else 'unknown'}' "
                        "does not support vision/image input"
                    )
                return func(*args, **kwargs)
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        return decorator
    
    def get_available_features(self) -> Dict[str, bool]:
        """
        Get a dictionary of all available features.
        
        Returns:
            Dict mapping feature names to availability
        """
        return {
            "tools": self.supports_tools,
            "vision": self.supports_vision,
            "streaming": self.supports_streaming,
            "json_mode": self.supports_json_mode
        }
    
    def get_ui_config(self) -> Dict[str, Any]:
        """
        Get UI configuration based on capabilities.
        
        Returns configuration for enabling/disabling UI elements.
        """
        return {
            "show_tool_panel": self.supports_tools,
            "show_image_upload": self.supports_vision,
            "enable_streaming": self.supports_streaming,
            "show_json_toggle": self.supports_json_mode,
            "mode_label": self._get_mode_label(),
            "mode_indicators": self._get_mode_indicators()
        }
    
    def _get_mode_label(self) -> str:
        """Generate a human-readable mode label."""
        parts = []
        if self.supports_tools:
            parts.append("Tools")
        else:
            parts.append("Chat Only")
        if self.supports_vision:
            parts.append("Vision")
        return " + ".join(parts)
    
    def _get_mode_indicators(self) -> list:
        """Generate mode indicator icons/emojis."""
        indicators = []
        if self.supports_tools:
            indicators.append({"icon": "🔧", "label": "Tools", "active": True})
        else:
            indicators.append({"icon": "💬", "label": "Chat Only", "active": False})
        if self.supports_vision:
            indicators.append({"icon": "👁️", "label": "Vision", "active": True})
        return indicators


# Global middleware instance (singleton pattern)
_middleware_instance: Optional[CapabilityMiddleware] = None


def get_capability_middleware() -> CapabilityMiddleware:
    """
    Get the global capability middleware instance.
    
    Returns:
        CapabilityMiddleware singleton
    """
    global _middleware_instance
    if _middleware_instance is None:
        _middleware_instance = CapabilityMiddleware()
    return _middleware_instance


async def validate_and_gate_provider(
    provider_name: str,
    model_name: str,
    provider_instance=None
) -> Tuple[ValidationResult, FeatureGate]:
    """
    Convenience function to validate a provider and get a feature gate.
    
    This is the main entry point for most use cases.
    
    Args:
        provider_name: Provider name
        model_name: Model name
        provider_instance: Optional provider instance for probing
        
    Returns:
        Tuple of (ValidationResult, FeatureGate)
        
    Usage:
        result, gate = await validate_and_gate_provider("ollama", "llama3.2:3b")
        
        if result.is_ready:
            if gate.supports_tools:
                agent.load_tools()
            if gate.supports_vision:
                agent.enable_vision()
    """
    middleware = get_capability_middleware()
    result = await middleware.validate_provider(
        provider_name=provider_name,
        model_name=model_name,
        provider_instance=provider_instance
    )
    
    gate = FeatureGate(result.capabilities)
    return result, gate
