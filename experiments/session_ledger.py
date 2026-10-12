"""Session Token Ledger (experiments prototype of Component C).

Tracks estimated vs actual input tokens per turn, computes running delta %,
and surfaces calibration hints. This is the feedback loop that tells us if
a provider's estimator is accurate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("logicore.experiments.session_ledger")

DEFAULT_TARGET_PCT = 5.0


@dataclass
class TurnTokenRecord:
    turn_id: str
    timestamp: str
    estimated_input: int
    actual_input: int
    actual_output: int
    delta_tokens: int
    delta_pct: float
    tokenizer_strategy: str
    accuracy_class: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "estimated_input": self.estimated_input,
            "actual_input": self.actual_input,
            "actual_output": self.actual_output,
            "delta_tokens": self.delta_tokens,
            "delta_pct": round(self.delta_pct, 2),
            "tokenizer_strategy": self.tokenizer_strategy,
            "accuracy_class": self.accuracy_class,
        }


@dataclass
class SessionTokenLedger:
    session_id: str
    provider: str
    model: str
    target_pct: float = DEFAULT_TARGET_PCT

    total_estimated_input: int = 0
    total_actual_input: int = 0
    total_actual_output: int = 0
    turn_count: int = 0
    turns: List[TurnTokenRecord] = field(default_factory=list)

    def record_turn(
        self,
        turn_id: str,
        estimated_input: int,
        actual_input: int,
        actual_output: int,
        tokenizer_strategy: str = "unknown",
        accuracy_class: str = "unknown",
    ) -> TurnTokenRecord:
        delta_tokens = abs(estimated_input - actual_input)
        delta_pct = (delta_tokens / actual_input * 100.0) if actual_input else 0.0
        record = TurnTokenRecord(
            turn_id=turn_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            estimated_input=estimated_input,
            actual_input=actual_input,
            actual_output=actual_output,
            delta_tokens=delta_tokens,
            delta_pct=delta_pct,
            tokenizer_strategy=tokenizer_strategy,
            accuracy_class=accuracy_class,
        )
        self.turns.append(record)
        self.total_estimated_input += estimated_input
        self.total_actual_input += actual_input
        self.total_actual_output += actual_output
        self.turn_count += 1
        if delta_pct > self.target_pct:
            logger.warning(
                "[SessionLedger] %s turn %s: estimated=%d actual=%d delta=%.1f%% (>%.0f%% target)",
                self.session_id,
                turn_id,
                estimated_input,
                actual_input,
                delta_pct,
                self.target_pct,
            )
        return record

    def record_canonical_turn(
        self,
        turn_id: str,
        estimated_input: int,
        canonical: Any,
        tokenizer_strategy: str = "unknown",
        accuracy_class: str = "unknown",
    ) -> TurnTokenRecord:
        """Record using a CanonicalUsage-like object (prompt_tokens/output_tokens)."""
        actual_input = getattr(canonical, "prompt_tokens", 0) or 0
        actual_output = getattr(canonical, "output_tokens", 0) or 0
        return self.record_turn(
            turn_id=turn_id,
            estimated_input=estimated_input,
            actual_input=actual_input,
            actual_output=actual_output,
            tokenizer_strategy=tokenizer_strategy,
            accuracy_class=accuracy_class,
        )

    @property
    def running_delta_pct(self) -> float:
        if not self.turns or self.total_actual_input == 0:
            return 0.0
        total_delta = sum(t.delta_tokens for t in self.turns)
        return total_delta / self.total_actual_input * 100.0

    @property
    def is_within_target(self) -> bool:
        return self.running_delta_pct <= self.target_pct

    @property
    def max_delta_pct(self) -> float:
        return max((t.delta_pct for t in self.turns), default=0.0)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "turns": self.turn_count,
            "running_delta_pct": round(self.running_delta_pct, 2),
            "is_within_target": self.is_within_target,
            "max_delta_pct": round(self.max_delta_pct, 2),
            "total_estimated_input": self.total_estimated_input,
            "total_actual_input": self.total_actual_input,
            "total_actual_output": self.total_actual_output,
        }

    def calibration_hint(self) -> Optional[str]:
        pct = self.running_delta_pct
        if pct > 10.0:
            return (
                f"Estimation error >10% — consider configuring a local tokenizer "
                f"for {self.provider}/{self.model}"
            )
        if pct < 1.0 and self.turn_count > 0:
            return f"Estimation is highly accurate for {self.provider}/{self.model}"
        return None


class LedgerHub:
    """Owns one SessionTokenLedger per session_id (in-memory, Phase 1)."""

    def __init__(self):
        self._ledgers: Dict[str, SessionTokenLedger] = {}

    def get_ledger(
        self,
        session_id: str,
        provider: str = "unknown",
        model: str = "default",
        target_pct: float = DEFAULT_TARGET_PCT,
    ) -> SessionTokenLedger:
        if session_id not in self._ledgers:
            self._ledgers[session_id] = SessionTokenLedger(
                session_id=session_id,
                provider=provider,
                model=model,
                target_pct=target_pct,
            )
        return self._ledgers[session_id]

    def record_turn_estimation(
        self,
        session_id: str,
        turn_id: str,
        estimated_input: int,
        canonical: Any,
        strategy: str = "unknown",
        accuracy_class: str = "unknown",
        provider: str = "unknown",
        model: str = "default",
    ) -> TurnTokenRecord:
        ledger = self.get_ledger(session_id, provider=provider, model=model)
        record = ledger.record_canonical_turn(
            turn_id=turn_id,
            estimated_input=estimated_input,
            canonical=canonical,
            tokenizer_strategy=strategy,
            accuracy_class=accuracy_class,
        )
        hint = ledger.calibration_hint()
        if hint:
            logger.info("[SessionLedger] %s: %s", session_id, hint)
        return record

    def all_summaries(self) -> List[Dict[str, Any]]:
        return [ledger.get_summary() for ledger in self._ledgers.values()]
