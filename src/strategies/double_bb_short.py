"""Double BB Short (이중 볼린저밴드) 전략 - Phase 4.

내측 BB(1σ)와 외측 BB(2σ) 사이의 가격 움직임 기반 매매.
"""

from __future__ import annotations

from typing import Any

from src.brokers.base import MarketData
from src.strategies.base import BacktestResult, Strategy, StrategyContext, TradeSignal


class DoubleBBShortStrategy(Strategy):
    def default_params(self) -> dict[str, Any]:
        return {
            "bb_period": 20,
            "inner_std": 1.0,
            "outer_std": 2.0,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "partial_tp_pct": 0.5,
            "stop_loss_mult": 1.0,
        }

    async def execute(self, ctx: StrategyContext) -> list[TradeSignal]:
        # Phase 4 구현
        return []

    async def backtest(self, historical: dict[str, list[MarketData]]) -> BacktestResult:
        return BacktestResult()


def create_strategy(
    capital_allocation: float = 100000,
    params: dict[str, Any] | None = None,
) -> DoubleBBShortStrategy:
    return DoubleBBShortStrategy(
        id="double_bb_short",
        name="Double BB Short",
        broker="upbit",
        symbols=["KRW-BTC", "KRW-ETH"],
        capital_allocation=capital_allocation,
        interval_minutes=5,
        params=params,
    )
