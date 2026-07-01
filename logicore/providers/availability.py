"""
Model Availability Service — Health tracking and automatic failover for LLM providers.

Architecture inspired by gemini-cli's modelAvailabilityService.ts:
- Tracks health state per provider: HEALTHY → UNHEALTHY_RETRY → UNHEALTHY_TERMINAL
- Classifies failures and determines retry eligibility
- Supports automatic fallback chains: primary → secondary → tertiary
- Integrates with ProviderGateway for seamless provider selection

Usage:
    availability = ModelAvailabilityService()
    availability.register_provider("primary", primary_provider, priority=1)
    availability.register_provider("secondary", secondary_provider, priority=2)
    
    # Get best available provider
    provider = availability.get_available_provider()
    
    # Report failures (auto-classifies and updates health)
    availability.report_failure("primary", error)
    
    # Manual recovery
    availability.mark_healthy("primary")
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Optional, List, Callable, Any
from datetime import datetime, timedelta
import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class HealthState(Enum):
    """Provider health states with different retry behaviors."""
    HEALTHY = "healthy"
    UNHEALTHY_RETRY = "unhealthy_retry"  # Temporary failure, will retry with backoff
    UNHEALTHY_TERMINAL = "unhealthy_terminal"  # Permanent failure, skip entirely
    COOLDOWN = "cooldown"  # Recently failed, waiting for cooldown period


class FailureCategory(Enum):
    """Classification of failure types for retry decisions."""
    RATE_LIMIT = "rate_limit"           # Retry with longer backoff
    NETWORK = "network"                  # Retry with standard backoff
    TIMEOUT = "timeout"                  # Retry with standard backoff
    AUTH = "auth"                        # Terminal - don't retry
    QUOTA_EXCEEDED = "quota_exceeded"    # Terminal - don't retry
    MODEL_NOT_FOUND = "model_not_found"  # Terminal - don't retry
    INVALID_REQUEST = "invalid_request"  # Terminal - don't retry
    SERVER_ERROR = "server_error"        # Retry with backoff
    UNKNOWN = "unknown"                  # Retry with standard backoff


@dataclass
class ProviderHealth:
    """Health state and metrics for a single provider."""
    provider_id: str
    provider: Any
    priority: int  # Lower = higher priority
    health_state: HealthState = HealthState.HEALTHY
    consecutive_failures: int = 0
    last_failure_time: Optional[datetime] = None
    last_failure_category: Optional[FailureCategory] = None
    last_success_time: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    total_requests: int = 0
    total_failures: int = 0
    total_successes: int = 0
    
    @property
    def failure_rate(self) -> float:
        """Calculate failure rate (0-1)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests
    
    @property
    def is_available(self) -> bool:
        """Check if provider is available for requests."""
        if self.health_state == HealthState.UNHEALTHY_TERMINAL:
            return False
        if self.health_state == HealthState.COOLDOWN:
            if self.cooldown_until and datetime.now() < self.cooldown_until:
                return False
            # Cooldown expired, transition to retry state
            return True
        return self.health_state in (HealthState.HEALTHY, HealthState.UNHEALTHY_RETRY)


@dataclass
class AvailabilityConfig:
    """Configuration for availability service behavior."""
    # Consecutive failures before marking as unhealthy
    failure_threshold: int = 3
    
    # Consecutive failures before marking as terminal
    terminal_threshold: int = 10
    
    # Base cooldown period (multiplied by consecutive failures)
    base_cooldown_seconds: float = 5.0
    
    # Maximum cooldown period
    max_cooldown_seconds: float = 300.0  # 5 minutes
    
    # Auto-recovery: check terminal providers after this duration
    terminal_check_interval: timedelta = timedelta(minutes=30)
    
    # Enable automatic health checks
    auto_health_check: bool = True
    
    # Health check interval
    health_check_interval: timedelta = timedelta(minutes=5)


class ModelAvailabilityService:
    """
    Manages provider health, automatic failover, and fallback chains.
    
    Key features:
    - Health state tracking per provider
    - Automatic failure classification
    - Exponential backoff cooldowns
    - Priority-based fallback chains
    - Thread-safe operations
    - Event callbacks for state changes
    """
    
    def __init__(self, config: Optional[AvailabilityConfig] = None):
        self.config = config or AvailabilityConfig()
        self._providers: Dict[str, ProviderHealth] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable[[str, HealthState, HealthState], None]] = []
        self._health_check_task: Optional[asyncio.Task] = None
        
    # -------------------------------------------------------------------------
    # Provider Registration
    # -------------------------------------------------------------------------
    
    def register_provider(
        self, 
        provider_id: str, 
        provider: Any, 
        priority: int = 100,
        initial_state: HealthState = HealthState.HEALTHY
    ) -> None:
        """Register a provider with the availability service."""
        with self._lock:
            self._providers[provider_id] = ProviderHealth(
                provider_id=provider_id,
                provider=provider,
                priority=priority,
                health_state=initial_state,
            )
            logger.debug(f"Registered provider '{provider_id}' with priority {priority}")
    
    def unregister_provider(self, provider_id: str) -> None:
        """Remove a provider from the availability service."""
        with self._lock:
            if provider_id in self._providers:
                del self._providers[provider_id]
                logger.debug(f"Unregistered provider '{provider_id}'")
    
    def get_provider(self, provider_id: str) -> Optional[Any]:
        """Get a provider by ID."""
        with self._lock:
            health = self._providers.get(provider_id)
            return health.provider if health else None
    
    # -------------------------------------------------------------------------
    # Provider Selection
    # -------------------------------------------------------------------------
    
    def get_available_provider(self) -> Optional[Any]:
        """Get the highest-priority available provider."""
        with self._lock:
            available = [
                h for h in self._providers.values() 
                if h.is_available
            ]
            if not available:
                logger.warning("No available providers found")
                return None
            
            # Sort by priority (lower = higher priority), then by failure rate
            available.sort(key=lambda h: (h.priority, h.failure_rate))
            selected = available[0]
            logger.debug(f"Selected provider '{selected.provider_id}' (priority={selected.priority})")
            return selected.provider
    
    def get_available_provider_id(self) -> Optional[str]:
        """Get the ID of the highest-priority available provider."""
        with self._lock:
            available = [
                h for h in self._providers.values() 
                if h.is_available
            ]
            if not available:
                return None
            available.sort(key=lambda h: (h.priority, h.failure_rate))
            return available[0].provider_id
    
    def get_fallback_chain(self) -> List[str]:
        """Get ordered list of available provider IDs for fallback."""
        with self._lock:
            available = [
                h for h in self._providers.values() 
                if h.is_available
            ]
            available.sort(key=lambda h: (h.priority, h.failure_rate))
            return [h.provider_id for h in available]
    
    def get_all_provider_ids(self) -> List[str]:
        """Get all registered provider IDs."""
        with self._lock:
            return list(self._providers.keys())
    
    # -------------------------------------------------------------------------
    # Health State Management
    # -------------------------------------------------------------------------
    
    def report_success(self, provider_id: str) -> None:
        """Report a successful request to a provider."""
        with self._lock:
            health = self._providers.get(provider_id)
            if not health:
                return
            
            old_state = health.health_state
            health.total_requests += 1
            health.total_successes += 1
            health.consecutive_failures = 0
            health.last_success_time = datetime.now()
            
            # Recover from unhealthy states
            if health.health_state in (HealthState.UNHEALTHY_RETRY, HealthState.COOLDOWN):
                health.health_state = HealthState.HEALTHY
                health.cooldown_until = None
                logger.info(f"Provider '{provider_id}' recovered to HEALTHY state")
                self._notify_state_change(provider_id, old_state, HealthState.HEALTHY)
    
    def report_failure(
        self, 
        provider_id: str, 
        error: Exception,
        category: Optional[FailureCategory] = None
    ) -> FailureCategory:
        """
        Report a failure to a provider. Returns the classified failure category.
        
        Args:
            provider_id: The provider that failed
            error: The exception that occurred
            category: Optional explicit category (auto-classified if not provided)
        
        Returns:
            The failure category used for this report
        """
        with self._lock:
            health = self._providers.get(provider_id)
            if not health:
                return FailureCategory.UNKNOWN
            
            # Classify the failure
            if category is None:
                category = self._classify_failure(error)
            
            old_state = health.health_state
            health.total_requests += 1
            health.total_failures += 1
            health.consecutive_failures += 1
            health.last_failure_time = datetime.now()
            health.last_failure_category = category
            
            # Determine new health state based on failure category and count
            new_state = self._determine_health_state(health, category)
            
            if new_state != old_state:
                health.health_state = new_state
                logger.info(
                    f"Provider '{provider_id}' state: {old_state.value} → {new_state.value} "
                    f"(category={category.value}, failures={health.consecutive_failures})"
                )
                self._notify_state_change(provider_id, old_state, new_state)
            
            # Apply cooldown if needed
            if new_state == HealthState.COOLDOWN:
                cooldown = self._calculate_cooldown(health, category)
                health.cooldown_until = datetime.now() + timedelta(seconds=cooldown)
                logger.debug(f"Provider '{provider_id}' cooldown: {cooldown:.1f}s")
            
            return category
    
    def mark_healthy(self, provider_id: str) -> None:
        """Manually mark a provider as healthy."""
        with self._lock:
            health = self._providers.get(provider_id)
            if not health:
                return
            
            old_state = health.health_state
            health.health_state = HealthState.HEALTHY
            health.consecutive_failures = 0
            health.cooldown_until = None
            
            if old_state != HealthState.HEALTHY:
                logger.info(f"Provider '{provider_id}' manually marked HEALTHY")
                self._notify_state_change(provider_id, old_state, HealthState.HEALTHY)
    
    def mark_terminal(self, provider_id: str, reason: str = "") -> None:
        """Manually mark a provider as terminal (permanently unavailable)."""
        with self._lock:
            health = self._providers.get(provider_id)
            if not health:
                return
            
            old_state = health.health_state
            health.health_state = HealthState.UNHEALTHY_TERMINAL
            
            if old_state != HealthState.UNHEALTHY_TERMINAL:
                logger.warning(f"Provider '{provider_id}' marked TERMINAL: {reason}")
                self._notify_state_change(provider_id, old_state, HealthState.UNHEALTHY_TERMINAL)
    
    def get_health(self, provider_id: str) -> Optional[ProviderHealth]:
        """Get health information for a provider."""
        with self._lock:
            return self._providers.get(provider_id)
    
    def get_all_health(self) -> Dict[str, ProviderHealth]:
        """Get health information for all providers."""
        with self._lock:
            return dict(self._providers)
    
    # -------------------------------------------------------------------------
    # Failure Classification
    # -------------------------------------------------------------------------
    
    def _classify_failure(self, error: Exception) -> FailureCategory:
        """Classify an exception into a failure category."""
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Rate limiting
        if any(x in error_str for x in ['rate limit', 'ratelimit', '429', 'too many requests', 'quota']):
            if 'quota' in error_str and 'exceeded' in error_str:
                return FailureCategory.QUOTA_EXCEEDED
            return FailureCategory.RATE_LIMIT
        
        # Authentication / Authorization
        if any(x in error_str for x in ['401', '403', 'unauthorized', 'authentication', 'invalid api key', 'invalid_api_key']):
            return FailureCategory.AUTH
        
        # Model not found
        if any(x in error_str for x in ['model not found', 'model_not_found', '404', 'does not exist', 'deployment not found']):
            return FailureCategory.MODEL_NOT_FOUND
        
        # Invalid request
        if any(x in error_str for x in ['400', 'bad request', 'invalid', 'validation error', 'malformed']):
            return FailureCategory.INVALID_REQUEST
        
        # Server errors
        if any(x in error_str for x in ['500', '502', '503', '504', 'internal server', 'service unavailable', 'bad gateway']):
            return FailureCategory.SERVER_ERROR
        
        # Network errors
        if any(x in error_type for x in ['connection', 'network', 'dns', 'socket']):
            return FailureCategory.NETWORK
        if any(x in error_str for x in ['connection', 'network error', 'unreachable', 'refused']):
            return FailureCategory.NETWORK
        
        # Timeout
        if any(x in error_str for x in ['timeout', 'timed out', 'deadline']):
            return FailureCategory.TIMEOUT
        if 'timeout' in error_type:
            return FailureCategory.TIMEOUT
        
        return FailureCategory.UNKNOWN
    
    def _determine_health_state(
        self, 
        health: ProviderHealth, 
        category: FailureCategory
    ) -> HealthState:
        """Determine the new health state based on failure category and history."""
        # Terminal failures immediately mark provider as terminal
        if category in (
            FailureCategory.AUTH, 
            FailureCategory.QUOTA_EXCEEDED,
            FailureCategory.MODEL_NOT_FOUND
        ):
            return HealthState.UNHEALTHY_TERMINAL
        
        # Check consecutive failure thresholds
        if health.consecutive_failures >= self.config.terminal_threshold:
            return HealthState.UNHEALTHY_TERMINAL
        
        if health.consecutive_failures >= self.config.failure_threshold:
            return HealthState.COOLDOWN
        
        # Single failure, apply short cooldown
        if health.consecutive_failures >= 1:
            return HealthState.COOLDOWN
        
        return HealthState.UNHEALTHY_RETRY
    
    def _calculate_cooldown(
        self, 
        health: ProviderHealth, 
        category: FailureCategory
    ) -> float:
        """Calculate cooldown period based on failure category and history."""
        base = self.config.base_cooldown_seconds
        
        # Rate limits get longer cooldowns
        if category == FailureCategory.RATE_LIMIT:
            base *= 4
        elif category == FailureCategory.SERVER_ERROR:
            base *= 2
        
        # Exponential backoff based on consecutive failures
        multiplier = min(2 ** (health.consecutive_failures - 1), 32)
        cooldown = base * multiplier
        
        return min(cooldown, self.config.max_cooldown_seconds)
    
    # -------------------------------------------------------------------------
    # Event Callbacks
    # -------------------------------------------------------------------------
    
    def on_state_change(
        self, 
        callback: Callable[[str, HealthState, HealthState], None]
    ) -> None:
        """Register a callback for health state changes."""
        self._callbacks.append(callback)
    
    def _notify_state_change(
        self, 
        provider_id: str, 
        old_state: HealthState, 
        new_state: HealthState
    ) -> None:
        """Notify all registered callbacks of a state change."""
        for callback in self._callbacks:
            try:
                callback(provider_id, old_state, new_state)
            except Exception as e:
                logger.warning(f"State change callback error: {e}")
    
    # -------------------------------------------------------------------------
    # Health Checks
    # -------------------------------------------------------------------------
    
    async def start_health_checks(self) -> None:
        """Start periodic health checks for terminal providers."""
        if not self.config.auto_health_check:
            return
        
        if self._health_check_task and not self._health_check_task.done():
            return
        
        self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def stop_health_checks(self) -> None:
        """Stop periodic health checks."""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None
    
    async def _health_check_loop(self) -> None:
        """Periodic health check loop."""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval.total_seconds())
                await self._check_terminal_providers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Health check error: {e}")
    
    async def _check_terminal_providers(self) -> None:
        """Check if terminal providers should be retried."""
        now = datetime.now()
        
        with self._lock:
            for health in self._providers.values():
                if health.health_state != HealthState.UNHEALTHY_TERMINAL:
                    continue
                
                # Check if enough time has passed since last failure
                if health.last_failure_time:
                    elapsed = now - health.last_failure_time
                    if elapsed >= self.config.terminal_check_interval:
                        # Transition to retry state to allow another attempt
                        old_state = health.health_state
                        health.health_state = HealthState.UNHEALTHY_RETRY
                        health.consecutive_failures = max(1, health.consecutive_failures // 2)
                        logger.info(f"Provider '{health.provider_id}' eligible for retry after terminal state")
                        self._notify_state_change(health.provider_id, old_state, HealthState.UNHEALTHY_RETRY)
    
    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------
    
    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all providers."""
        with self._lock:
            stats = {}
            for provider_id, health in self._providers.items():
                stats[provider_id] = {
                    "state": health.health_state.value,
                    "priority": health.priority,
                    "is_available": health.is_available,
                    "consecutive_failures": health.consecutive_failures,
                    "total_requests": health.total_requests,
                    "total_successes": health.total_successes,
                    "total_failures": health.total_failures,
                    "failure_rate": round(health.failure_rate, 4),
                    "last_failure_category": health.last_failure_category.value if health.last_failure_category else None,
                    "cooldown_until": health.cooldown_until.isoformat() if health.cooldown_until else None,
                }
            return stats
    
    def reset_stats(self, provider_id: Optional[str] = None) -> None:
        """Reset statistics for a provider or all providers."""
        with self._lock:
            providers = [provider_id] if provider_id else list(self._providers.keys())
            for pid in providers:
                health = self._providers.get(pid)
                if health:
                    health.total_requests = 0
                    health.total_successes = 0
                    health.total_failures = 0
