"""
Skill Bridge — async adapter connecting core/signal_bus to SwarmConsensus.

Dependency flow:
  skills/*.py  →  core/signal_bus.py  →  skill_bridge.py  →  SwarmConsensus
                                                           →  Executor

Called by the scheduler on each tick (or from test code directly).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core import signal_bus
from src.agents.models import AgentContext
from src.config import settings
from src.strategies.base import TradeSignal

if TYPE_CHECKING:
    from src.agents.swarm import SwarmConsensus
    from src.engine.executor import Executor

logger = logging.getLogger(__name__)


def _to_trade_signal(sig: dict) -> TradeSignal:
    """Convert core.Signal dict → src TradeSignal.

    Mapping rules:
      direction 'long'  → side 'buy'
      direction 'short' → side 'sell'
      score / 100       → confidence (capped at 1.0)
      source + regime   → reason string
    """
    direction = sig.get("direction", "long")
    side = "buy" if direction == "long" else "sell"

    score = float(sig.get("score", 50))
    confidence = min(score / 100.0, 1.0)

    source = sig.get("source", "skill")
    regime = sig.get("regime", "neutral")
    reason = f"[{source}] regime={regime} score={score:.1f}"

    symbol = sig.get("symbol", "")
    exchange = sig.get("exchange", "upbit")

    return TradeSignal(
        side=side,
        symbol=symbol,
        amount=0.0,       # sizing is handled downstream by risk_manager
        confidence=confidence,
        reason=reason,
        exchange=exchange,
    )


def _build_context(sig: dict) -> AgentContext:
    """Build AgentContext from signal meta fields for SwarmConsensus."""
    meta = sig.get("meta") or {}
    return AgentContext(
        market_data={
            "rsi":       meta.get("rsi", 50.0),
            "vol_ratio": meta.get("vol_ratio", 1.0),
            "change_1h": meta.get("change_1h", 0.0),
            "change_15m": meta.get("change_15m", 0.0),
            "regime":    sig.get("regime", "neutral"),
        }
    )


async def process_pending(
    swarm: "SwarmConsensus | None" = None,
    executor: "Executor | None" = None,
) -> list[dict]:
    """Drain pending signals and route them through the pipeline.

    Returns a list of result dicts for logging/testing.
    """
    raw_signals = list(signal_bus.drain())
    if not raw_signals:
        return []

    results = []
    for sig in raw_signals:
        symbol = sig.get("symbol", "?")
        try:
            trade_signal = _to_trade_signal(sig)
        except Exception as exc:
            logger.warning("skill_bridge: failed to parse signal %s — %s", symbol, exc)
            signal_bus.reject(sig, reason=f"parse_error: {exc}")
            results.append({"symbol": symbol, "status": "rejected", "reason": str(exc)})
            continue

        # Skip signals below minimum confidence threshold
        if trade_signal.confidence < settings.swarm_min_confidence:
            reason = (
                f"confidence {trade_signal.confidence:.2f} < "
                f"min {settings.swarm_min_confidence}"
            )
            signal_bus.reject(sig, reason=reason)
            results.append({"symbol": symbol, "status": "rejected", "reason": reason})
            continue

        # Optional SwarmConsensus gate
        if swarm and settings.swarm_enabled:
            ctx = _build_context(sig)
            try:
                consensus = await swarm.evaluate(trade_signal, ctx)
            except Exception as exc:
                logger.error("skill_bridge: swarm error for %s — %s", symbol, exc)
                signal_bus.reject(sig, reason=f"swarm_error: {exc}")
                results.append({"symbol": symbol, "status": "error", "reason": str(exc)})
                continue

            if not consensus.approved:
                reason = f"swarm_rejected quorum={consensus.quorum_str}"
                signal_bus.reject(sig, reason=reason)
                results.append({
                    "symbol": symbol,
                    "status": "rejected",
                    "reason": reason,
                    "consensus": consensus.to_dict(),
                })
                continue

            logger.info(
                "skill_bridge: swarm approved %s quorum=%s",
                symbol,
                consensus.quorum_str,
            )

        # Forward to executor if provided
        if executor:
            try:
                await executor.execute(trade_signal)
                results.append({"symbol": symbol, "status": "executed"})
            except Exception as exc:
                logger.error("skill_bridge: executor error for %s — %s", symbol, exc)
                results.append({"symbol": symbol, "status": "exec_error", "reason": str(exc)})
        else:
            results.append({"symbol": symbol, "status": "approved"})

    return results
