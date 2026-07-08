"""
Resilient gateway wrapper with automatic retry, failover, and health tracking.
"""

from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING
import asyncio
import logging

from .base import ProviderGateway, NormalizedMessage, _dispatch_token

if TYPE_CHECKING:
    from .availability import ModelAvailabilityService
    from .policies import RetryPolicy

logger = logging.getLogger(__name__)


def get_gateway_for_provider(provider) -> ProviderGateway:
    """Get the appropriate gateway for a provider instance."""
    from .openai_gateway import OpenAIGateway
    from .gemini_gateway import GeminiGateway
    from .azure_gateway import AzureGateway
    from .ollama_gateway import OllamaGateway

    provider_name = getattr(provider, "provider_name", "unknown").lower()

    GATEWAY_MAP = {
        "openai": OpenAIGateway,
        "groq": OpenAIGateway,
        "custom": OpenAIGateway,
        "gemini": GeminiGateway,
        "azure": AzureGateway,
        "ollama": OllamaGateway,
    }

    gateway_class = GATEWAY_MAP.get(provider_name, OpenAIGateway)
    return gateway_class(provider)


class ResilientGateway(ProviderGateway):
    """
    Gateway wrapper that adds resilient features:
    - Automatic retry with exponential backoff
    - Provider health tracking via ModelAvailabilityService
    - Automatic failover to fallback providers
    - Mid-stream retry for streaming failures (configurable)
    """

    def __init__(
        self,
        provider,
        availability: Optional["ModelAvailabilityService"] = None,
        retry_policy: Optional["RetryPolicy"] = None,
        enable_failover: bool = True,
        enable_stream_retry: bool = True,
        max_stream_retries: int = 3,
    ):
        super().__init__(provider)
        self.availability = availability
        self.enable_failover = enable_failover
        self.enable_stream_retry = enable_stream_retry
        self.max_stream_retries = max_stream_retries

        from .policies import DEFAULT_RETRY_POLICY
        self.retry_policy = retry_policy or DEFAULT_RETRY_POLICY

        self._inner_gateway = get_gateway_for_provider(provider)

        self._current_provider = provider
        self._current_provider_id = getattr(provider, 'get_provider_id', lambda: 'default')()

    def _get_provider_id(self, provider) -> str:
        if hasattr(provider, 'get_provider_id'):
            return provider.get_provider_id()
        return f"{getattr(provider, 'provider_name', 'unknown')}:{getattr(provider, 'model_name', 'unknown')}"

    def _switch_provider(self, provider) -> None:
        self._current_provider = provider
        self._current_provider_id = self._get_provider_id(provider)
        self._inner_gateway = get_gateway_for_provider(provider)
        self.model_name = provider.get_model_name() if hasattr(provider, 'get_model_name') else provider.model_name
        logger.info(f"Switched to provider: {self._current_provider_id}")

    async def _try_failover(self) -> bool:
        if not self.availability or not self.enable_failover:
            return False

        chain = self.availability.get_fallback_chain()

        current_found = False
        for provider_id in chain:
            if provider_id == self._current_provider_id:
                current_found = True
                continue
            if current_found:
                new_provider = self.availability.get_provider(provider_id)
                if new_provider:
                    self._switch_provider(new_provider)
                    return True

        return False

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> NormalizedMessage:
        from .policies import RetryIterator
        from .availability import FailureCategory

        last_error = None
        providers_tried = set()

        while True:
            provider_id = self._current_provider_id
            providers_tried.add(provider_id)

            async for attempt in RetryIterator(
                self.retry_policy,
                self.availability,
                provider_id,
            ):
                try:
                    result = await self._inner_gateway.chat(messages, tools=tools)

                    if self.availability:
                        self.availability.report_success(provider_id)

                    return result

                except Exception as e:
                    last_error = e

                    category = None
                    if self.availability:
                        category = self.availability.report_failure(provider_id, e)

                    try:
                        await attempt.handle_failure(e, category)
                    except Exception:
                        break

            if await self._try_failover():
                if self._current_provider_id not in providers_tried:
                    logger.info(f"Failing over from {provider_id} to {self._current_provider_id}")
                    continue

            break

        if last_error:
            raise last_error
        raise RuntimeError("Chat failed without capturing an error")

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> NormalizedMessage:
        from .policies import RetryIterator

        last_error = None
        providers_tried = set()
        stream_retries = 0

        while True:
            provider_id = self._current_provider_id
            providers_tried.add(provider_id)

            async for attempt in RetryIterator(
                self.retry_policy,
                self.availability,
                provider_id,
            ):
                try:
                    accumulated_content = ""

                    async def capturing_on_token(token: str):
                        nonlocal accumulated_content
                        accumulated_content += token
                        if on_token:
                            await _dispatch_token(on_token, token)

                    result = await self._inner_gateway.chat_stream(
                        messages,
                        tools=tools,
                        on_token=capturing_on_token
                    )

                    if self.availability:
                        self.availability.report_success(provider_id)

                    return result

                except Exception as e:
                    last_error = e

                    if self.enable_stream_retry and stream_retries < self.max_stream_retries:
                        error_str = str(e).lower()
                        is_stream_error = any(x in error_str for x in [
                            'stream', 'chunk', 'incomplete', 'partial',
                            'connection reset', 'connection closed'
                        ])

                        if is_stream_error:
                            stream_retries += 1
                            logger.warning(
                                f"Mid-stream retry {stream_retries}/{self.max_stream_retries} "
                                f"for provider {provider_id}"
                            )
                            await asyncio.sleep(0.5 * (2 ** (stream_retries - 1)))
                            continue

                    category = None
                    if self.availability:
                        category = self.availability.report_failure(provider_id, e)

                    try:
                        await attempt.handle_failure(e, category)
                    except Exception:
                        break

            if await self._try_failover():
                if self._current_provider_id not in providers_tried:
                    stream_retries = 0
                    continue

            break

        if last_error:
            raise last_error
        raise RuntimeError("Chat stream failed without capturing an error")


def get_resilient_gateway(
    provider,
    availability: Optional["ModelAvailabilityService"] = None,
    retry_policy: Optional["RetryPolicy"] = None,
    **kwargs
) -> ResilientGateway:
    """Factory function for creating a ResilientGateway."""
    return ResilientGateway(
        provider=provider,
        availability=availability,
        retry_policy=retry_policy,
        **kwargs
    )
