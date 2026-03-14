"""백테스트 엔진 - Phase 2에서 구현"""

from __future__ import annotations

from src.brokers.base import MarketData
from src.strategies.base import BacktestResult, Strategy


class BacktestEngine:
    async def run(
        self,
        strategy: Strategy,
        historical: dict[str, list[MarketData]],
        initial_capital: float = 100000,
    ) -> BacktestResult:
        """전략 백테스트 실행"""
        return await strategy.backtest(historical)
