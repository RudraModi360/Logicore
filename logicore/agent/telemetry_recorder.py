"""
TelemetryRecorder: Handles telemetry recording extracted from ChatOrchestrator.

Consolidates usage normalization, provider-specific prefix cache estimation,
token counter accumulation, cost estimation, storage persistence, and
stream event emission into a single focused module.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from logicore.agent.agent_protocol import AgentProtocol

logger = logging.getLogger(__name__)


class TelemetryRecorder:
    """Records telemetry for LLM calls.

    Extracted from ChatOrchestrator._record_telemetry to separate
    telemetry concerns from the chat loop.
    """

    def __init__(self, debug: bool = False):
        self.debug = debug

    def record(
        self,
        agent: "AgentProtocol",
        session_id: str,
        llm_start_time: float,
        response: Any,
        tool_calls: Optional[List[Dict[str, Any]]],
        emitter: Any = None,
    ) -> None:
        """Record telemetry from a single LLM call.

        Always accumulates in memory. DB persistence only if session row exists
        (final _persist_session handles the definitive write).
        """
        try:
            from logicore.telemetry.canonical import normalize_usage
            from logicore.telemetry.pricing import estimate_usage_cost

            llm_end_time = time.time()
            duration_ms = (llm_end_time - llm_start_time) * 1000

            raw_usage = getattr(response, "usage", None)
            provider_name = getattr(agent.provider, "provider_name", "unknown")

            canonical = normalize_usage(raw_usage, provider=provider_name)

            # Auto-prefix-cache estimation for Ollama and Gemini
            canonical = self._estimate_prefix_cache(
                canonical, raw_usage, provider_name, session_id, agent
            )

            # Accumulate token counters on agent
            agent.session_input_tokens += canonical.input_tokens
            agent.session_output_tokens += canonical.output_tokens
            agent.session_cache_read_tokens += canonical.cache_read_tokens
            agent.session_cache_write_tokens += canonical.cache_write_tokens
            agent.session_reasoning_tokens += canonical.reasoning_tokens
            agent.session_api_calls += 1

            # Cost estimation
            base_url = getattr(agent.provider, "base_url", None)
            api_key = getattr(agent.provider, "api_key", None)
            cost = estimate_usage_cost(
                agent.model_name, canonical,
                provider=provider_name, base_url=base_url, api_key=api_key,
            )
            agent.session_estimated_cost_usd += float(cost.amount_usd or 0)
            agent.session_cost_status = cost.status
            agent.session_cost_source = cost.source

            # Storage persistence
            self._persist_to_storage(agent, session_id, canonical, tool_calls, cost)

            # Logging
            self._log_telemetry(agent, canonical, cost)

            # Tracker recording
            self._record_to_tracker(agent, session_id, canonical, duration_ms, tool_calls)

            # Stream event emission
            self._emit_usage_event(agent, emitter, canonical, cost, session_id)

        except Exception as e:
            if self.debug:
                logger.error(f"[TelemetryRecorder] Telemetry error: {e}")

    def _estimate_prefix_cache(
        self,
        canonical: Any,
        raw_usage: Any,
        provider_name: str,
        session_id: str,
        agent: "AgentProtocol",
    ) -> Any:
        """Estimate prefix cache for Ollama and Gemini providers.

        Both providers do transparent prefix caching but report differently:
        - Ollama: prompt_tokens INCREASES between turns (reports total including cached)
        - Gemini: prompt_tokens DECREASES between turns (reports only uncached)
        """
        if provider_name not in ("ollama", "gemini") or not raw_usage:
            return canonical

        prompt_tokens = 0
        if isinstance(raw_usage, dict):
            prompt_tokens = raw_usage.get("prompt_tokens", 0)
        else:
            prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0

        if prompt_tokens <= 0:
            return canonical

        prev_key = f"_prefix_cache_prev_{session_id}"
        prev_prompt = getattr(agent, "_prefix_cache_tracker", {}).get(prev_key, 0)

        if prev_prompt > 0:
            from logicore.telemetry.canonical import CanonicalUsage
            if provider_name == "ollama" and prompt_tokens > prev_prompt:
                canonical = CanonicalUsage(
                    input_tokens=prompt_tokens - prev_prompt,
                    output_tokens=canonical.output_tokens,
                    cache_read_tokens=prev_prompt,
                    cache_write_tokens=canonical.cache_write_tokens,
                    reasoning_tokens=canonical.reasoning_tokens,
                )
            elif provider_name == "gemini" and prompt_tokens < prev_prompt:
                canonical = CanonicalUsage(
                    input_tokens=prompt_tokens,
                    output_tokens=canonical.output_tokens,
                    cache_read_tokens=prev_prompt - prompt_tokens,
                    cache_write_tokens=canonical.cache_write_tokens,
                    reasoning_tokens=canonical.reasoning_tokens,
                )

        # Store for next turn
        if not hasattr(agent, "_prefix_cache_tracker"):
            agent._prefix_cache_tracker = {}
        agent._prefix_cache_tracker[prev_key] = prompt_tokens

        return canonical

    def _persist_to_storage(
        self,
        agent: "AgentProtocol",
        session_id: str,
        canonical: Any,
        tool_calls: Optional[List[Dict[str, Any]]],
        cost: Any,
    ) -> None:
        """Persist telemetry to storage if available."""
        if not (agent._storage and agent._storage.initialized):
            return
        if not agent._storage.session_exists(session_id):
            return
        agent._storage.save_telemetry(
            session_id,
            input_tokens=canonical.input_tokens,
            output_tokens=canonical.output_tokens,
            cache_read_tokens=canonical.cache_read_tokens,
            cache_write_tokens=canonical.cache_write_tokens,
            reasoning_tokens=canonical.reasoning_tokens,
            tool_calls=len(tool_calls) if tool_calls else 0,
            api_calls=1,
            estimated_cost_usd=float(cost.amount_usd or 0),
            cost_status=cost.status,
        )

    def _log_telemetry(
        self,
        agent: "AgentProtocol",
        canonical: Any,
        cost: Any,
    ) -> None:
        """Log telemetry data."""
        if not (agent.telemetry_enabled or self.debug):
            return
        logger.info(
            f"[Telemetry] in={canonical.input_tokens} out={canonical.output_tokens} "
            f"cache_r={canonical.cache_read_tokens} cache_w={canonical.cache_write_tokens} "
            f"reasoning={canonical.reasoning_tokens} total={canonical.total_tokens} "
            f"cost={cost.label} session_total_in={agent.session_input_tokens} "
            f"session_total_out={agent.session_output_tokens} "
            f"session_api_calls={agent.session_api_calls}"
        )

    def _record_to_tracker(
        self,
        agent: "AgentProtocol",
        session_id: str,
        canonical: Any,
        duration_ms: float,
        tool_calls: Optional[List[Dict[str, Any]]],
    ) -> None:
        """Record to telemetry tracker."""
        if not (agent.telemetry_enabled and hasattr(agent, "telemetry_tracker") and agent.telemetry_tracker):
            return
        agent.telemetry_tracker.record_request(
            session_id=session_id,
            input_tokens=canonical.input_tokens,
            output_tokens=canonical.output_tokens,
            model=agent.model_name,
            provider=getattr(agent.provider, "provider_name", "unknown"),
            duration_ms=duration_ms,
            tool_calls=len(tool_calls) if tool_calls else 0,
        )

    def _emit_usage_event(
        self,
        agent: "AgentProtocol",
        emitter: Any,
        canonical: Any,
        cost: Any,
        session_id: str,
    ) -> None:
        """Emit usage event to stream emitter."""
        if not (agent.telemetry_enabled and emitter):
            return
        try:
            from logicore.stream.events import StreamEvent, StreamEventType
            emitter.emit(StreamEvent.create(StreamEventType.USAGE, {
                "input_tokens": canonical.input_tokens,
                "output_tokens": canonical.output_tokens,
                "cache_read_tokens": canonical.cache_read_tokens,
                "cache_write_tokens": canonical.cache_write_tokens,
                "reasoning_tokens": canonical.reasoning_tokens,
                "total_tokens": canonical.total_tokens,
                "api_calls": agent.session_api_calls,
                "estimated_cost_usd": agent.session_estimated_cost_usd,
                "cost_status": cost.status,
                "session_id": session_id,
            }))
        except Exception:
            pass
