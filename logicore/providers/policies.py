"""
Provider Retry Policies — Configurable retry behavior and fallback resolution.

Provides:
- RetryPolicy: Configurable retry behavior per failure type
- ExponentialBackoff: Backoff calculation with jitter
- FallbackResolver: Resolves fallback chain from availability service
- with_retry: Async decorator for automatic retries

Usage:
    # Configure retry policy
    policy = RetryPolicy(
        max_attempts=5,
        base_delay=1.0,
        max_delay=60.0,
        retry_categories={FailureCategory.RATE_LIMIT, FailureCategory.NETWORK}
    )
    
    # Use decorator
    @with_retry(policy)
    async def call_llm():
        ...
    
    # Or use manually
    async for attempt in policy.attempts():
        try:
            result = await call_llm()
            break
        except Exception as e:
            await attempt.handle_failure(e)
"""

from dataclasses import dataclass, field
from typing import Set, Optional, Callable, Any, List
import asyncio
import random
import logging
import functools

from .availability import FailureCategory, ModelAvailabilityService

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    """
    Configurable retry policy for LLM provider calls.
    
    Attributes:
        max_attempts: Maximum number of attempts (including initial)
        base_delay: Base delay in seconds before first retry
        max_delay: Maximum delay in seconds (caps exponential backoff)
        exponential_base: Base for exponential backoff calculation
        jitter: Random jitter factor (0-1) to prevent thundering herd
        retry_categories: Set of failure categories that should be retried
        terminal_categories: Set of failure categories that should never be retried
    """
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: float = 0.1
    retry_categories: Set[FailureCategory] = field(default_factory=lambda: {
        FailureCategory.RATE_LIMIT,
        FailureCategory.NETWORK,
        FailureCategory.TIMEOUT,
        FailureCategory.SERVER_ERROR,
        FailureCategory.UNKNOWN,
    })
    terminal_categories: Set[FailureCategory] = field(default_factory=lambda: {
        FailureCategory.AUTH,
        FailureCategory.QUOTA_EXCEEDED,
        FailureCategory.MODEL_NOT_FOUND,
        FailureCategory.INVALID_REQUEST,
    })
    
    def should_retry(self, category: FailureCategory, attempt: int) -> bool:
        """Check if a failure should be retried."""
        if attempt >= self.max_attempts:
            return False
        if category in self.terminal_categories:
            return False
        return category in self.retry_categories
    
    def calculate_delay(self, attempt: int, category: FailureCategory) -> float:
        """Calculate delay before next retry attempt."""
        # Exponential backoff
        delay = self.base_delay * (self.exponential_base ** (attempt - 1))
        
        # Rate limits get extra delay
        if category == FailureCategory.RATE_LIMIT:
            delay *= 2
        
        # Cap at max delay
        delay = min(delay, self.max_delay)
        
        # Add jitter
        jitter_amount = delay * self.jitter * random.random()
        delay += jitter_amount
        
        return delay


@dataclass
class RetryAttempt:
    """Represents a single retry attempt with context."""
    attempt_number: int
    max_attempts: int
    policy: RetryPolicy
    availability: Optional[ModelAvailabilityService] = None
    provider_id: Optional[str] = None
    
    @property
    def is_last_attempt(self) -> bool:
        """Check if this is the final attempt."""
        return self.attempt_number >= self.max_attempts
    
    @property
    def remaining_attempts(self) -> int:
        """Get number of remaining attempts."""
        return max(0, self.max_attempts - self.attempt_number)
    
    async def handle_failure(
        self, 
        error: Exception,
        category: Optional[FailureCategory] = None
    ) -> None:
        """
        Handle a failure during this attempt.
        
        Reports the failure to availability service and calculates delay.
        Raises the error if no more retries should be attempted.
        """
        # Classify the failure
        if category is None and self.availability:
            category = self.availability._classify_failure(error)
        elif category is None:
            category = _classify_failure_standalone(error)
        
        # Report to availability service
        if self.availability and self.provider_id:
            self.availability.report_failure(self.provider_id, error, category)
        
        # Check if we should retry
        if not self.policy.should_retry(category, self.attempt_number):
            logger.debug(
                f"Not retrying after attempt {self.attempt_number}: "
                f"category={category.value}"
            )
            raise error
        
        # Calculate and apply delay
        delay = self.policy.calculate_delay(self.attempt_number, category)
        logger.debug(
            f"Retry attempt {self.attempt_number + 1}/{self.max_attempts} "
            f"after {delay:.2f}s (category={category.value})"
        )
        await asyncio.sleep(delay)


class RetryIterator:
    """
    Async iterator that yields retry attempts.
    
    Usage:
        async for attempt in RetryIterator(policy):
            try:
                result = await some_operation()
                break
            except Exception as e:
                await attempt.handle_failure(e)
    """
    
    def __init__(
        self, 
        policy: RetryPolicy,
        availability: Optional[ModelAvailabilityService] = None,
        provider_id: Optional[str] = None,
    ):
        self.policy = policy
        self.availability = availability
        self.provider_id = provider_id
        self._attempt = 0
    
    def __aiter__(self) -> "RetryIterator":
        return self
    
    async def __anext__(self) -> RetryAttempt:
        self._attempt += 1
        if self._attempt > self.policy.max_attempts:
            raise StopAsyncIteration
        
        return RetryAttempt(
            attempt_number=self._attempt,
            max_attempts=self.policy.max_attempts,
            policy=self.policy,
            availability=self.availability,
            provider_id=self.provider_id,
        )


def _classify_failure_standalone(error: Exception) -> FailureCategory:
    """Classify an exception without availability service context."""
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()
    
    if any(x in error_str for x in ['rate limit', 'ratelimit', '429', 'too many requests']):
        return FailureCategory.RATE_LIMIT
    if any(x in error_str for x in ['401', '403', 'unauthorized', 'authentication', 'invalid api key']):
        return FailureCategory.AUTH
    if any(x in error_str for x in ['model not found', '404', 'does not exist']):
        return FailureCategory.MODEL_NOT_FOUND
    if any(x in error_str for x in ['400', 'bad request', 'invalid', 'validation']):
        return FailureCategory.INVALID_REQUEST
    if any(x in error_str for x in ['500', '502', '503', '504', 'internal server']):
        return FailureCategory.SERVER_ERROR
    if any(x in error_type for x in ['connection', 'network']):
        return FailureCategory.NETWORK
    if any(x in error_str for x in ['timeout', 'timed out']):
        return FailureCategory.TIMEOUT
    
    return FailureCategory.UNKNOWN


def with_retry(
    policy: Optional[RetryPolicy] = None,
    availability: Optional[ModelAvailabilityService] = None,
    provider_id_arg: Optional[str] = None,
) -> Callable:
    """
    Decorator for automatic retry with exponential backoff.
    
    Args:
        policy: Retry policy to use (defaults to standard policy)
        availability: Optional availability service for health tracking
        provider_id_arg: Name of the argument containing provider_id (for dynamic lookup)
    
    Usage:
        @with_retry(policy=RetryPolicy(max_attempts=3))
        async def call_llm(messages):
            ...
        
        @with_retry(availability=service, provider_id_arg="provider_id")
        async def call_provider(provider_id, messages):
            ...
    """
    if policy is None:
        policy = RetryPolicy()
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Extract provider_id from arguments if specified
            provider_id = None
            if provider_id_arg:
                provider_id = kwargs.get(provider_id_arg)
            
            last_error = None
            
            async for attempt in RetryIterator(policy, availability, provider_id):
                try:
                    result = await func(*args, **kwargs)
                    # Report success
                    if availability and provider_id:
                        availability.report_success(provider_id)
                    return result
                except Exception as e:
                    last_error = e
                    try:
                        await attempt.handle_failure(e)
                    except Exception:
                        raise e
            
            # Should not reach here, but raise last error if we do
            if last_error:
                raise last_error
        
        return wrapper
    return decorator


class FallbackResolver:
    """
    Resolves fallback chain from availability service.
    
    Provides iteration over available providers in priority order,
    with automatic failover on errors.
    """
    
    def __init__(
        self, 
        availability: ModelAvailabilityService,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self.availability = availability
        self.retry_policy = retry_policy or RetryPolicy()
    
    def get_fallback_chain(self) -> List[str]:
        """Get ordered list of available provider IDs."""
        return self.availability.get_fallback_chain()
    
    async def execute_with_fallback(
        self,
        operation: Callable[[Any], Any],
        *args,
        **kwargs
    ) -> Any:
        """
        Execute an operation with automatic fallback to other providers.
        
        Args:
            operation: Async callable that takes (provider, *args, **kwargs)
            *args, **kwargs: Arguments to pass to the operation
        
        Returns:
            Result from the first successful provider
        
        Raises:
            Exception: If all providers fail, raises the last error
        """
        chain = self.get_fallback_chain()
        if not chain:
            raise RuntimeError("No providers available in fallback chain")
        
        last_error = None
        
        for provider_id in chain:
            provider = self.availability.get_provider(provider_id)
            if provider is None:
                continue
            
            # Try this provider with retries
            async for attempt in RetryIterator(
                self.retry_policy, 
                self.availability, 
                provider_id
            ):
                try:
                    result = await operation(provider, *args, **kwargs)
                    self.availability.report_success(provider_id)
                    return result
                except Exception as e:
                    last_error = e
                    category = self.availability._classify_failure(e)
                    
                    # If terminal failure, skip remaining retries for this provider
                    if category in self.retry_policy.terminal_categories:
                        logger.debug(
                            f"Terminal failure for '{provider_id}', "
                            f"trying next provider"
                        )
                        break
                    
                    # Try to handle the failure (may raise if no more retries)
                    try:
                        await attempt.handle_failure(e, category)
                    except Exception:
                        # No more retries for this provider, try next
                        break
        
        # All providers exhausted
        if last_error:
            raise last_error
        raise RuntimeError("All providers failed without capturing an error")


@dataclass
class RetryStats:
    """Statistics for retry operations."""
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    total_delay_seconds: float = 0.0
    failures_by_category: dict = field(default_factory=dict)
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate (0-1)."""
        if self.total_attempts == 0:
            return 0.0
        return self.successful_attempts / self.total_attempts
    
    def record_attempt(
        self, 
        success: bool, 
        delay: float = 0.0,
        category: Optional[FailureCategory] = None
    ) -> None:
        """Record an attempt result."""
        self.total_attempts += 1
        self.total_delay_seconds += delay
        
        if success:
            self.successful_attempts += 1
        else:
            self.failed_attempts += 1
            if category:
                key = category.value
                self.failures_by_category[key] = self.failures_by_category.get(key, 0) + 1


# Convenience instances for common retry policies
DEFAULT_RETRY_POLICY = RetryPolicy()

AGGRESSIVE_RETRY_POLICY = RetryPolicy(
    max_attempts=10,
    base_delay=0.5,
    max_delay=30.0,
)

CONSERVATIVE_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=2.0,
    max_delay=120.0,
)

NO_RETRY_POLICY = RetryPolicy(
    max_attempts=1,
)
