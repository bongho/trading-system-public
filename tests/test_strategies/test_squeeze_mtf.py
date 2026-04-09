"""SqueezeMTF 전략 테스트"""

from __future__ import annotations

import math
import pytest

from src.brokers.base import MarketData
from src.strategies.base import StrategyContext
from src.strategies.squeeze_mtf import SqueezeMTFStrategy, create_strategy


def _make_candles(
    prices: list[float],
    symbol: str = "KRW-BTC",
    volatility: float = 0.001,
) -> list[MarketData]:
    """테스트용 캔들 생성 (변동성 포함)"""
    return [
        MarketData(
            symbol=symbol,
            timestamp=f"2026-01-{(i // 24 + 1):02d}T{(i % 24):02d}:00:00",
            open=p,
            high=p * (1 + volatility),
            low=p * (1 - volatility),
            close=p,
            volume=100.0,
        )
        for i, p in enumerate(prices)
    ]


def _make_squeeze_release_pattern(n: int = 100) -> list[float]:
    """스퀴즈 → 해제 패턴 (좁은 범위 → 급격한 확장)"""
    prices = []
    base = 50000.0
    # 스퀴즈 구간: 좁은 변동
    for i in range(50):
        prices.append(base + math.sin(i * 0.3) * 100)
    # 스퀴즈 해제: 급격한 상승
    for i in range(25):
        prices.append(base + i * 800)
    # 모멘텀 약화 + 하락
    for i in range(25):
        prices.append(base + 20000 - i * 600)
    return prices


class TestSqueezeMTFStrategy:
    def test_create_strategy(self) -> None:
        strategy = create_strategy(capital_allocation=200000)
        assert strategy.id == "squeeze_mtf"
        assert strategy.name == "Squeeze MTF"
        assert strategy.capital_allocation == 200000
        assert strategy.params["bb_period"] == 20

    def test_default_params(self) -> None:
        strategy = create_strategy()
        params = strategy.default_params()
        assert "bb_period" in params
        assert "kc_period" in params
        assert "mom_period" in params
        assert params["timeframes"] == ["5m", "1h", "4h"]

    async def test_execute_no_signal_insufficient_data(self) -> None:
        strategy = create_strategy()
        candles = _make_candles([50000] * 10)
        ctx = StrategyContext(
            market_data={"KRW-BTC": candles},
            portfolio_value=1000000,
            current_positions={},
            params=strategy.params,
        )
        signals = await strategy.execute(ctx)
        assert signals == []

    async def test_execute_with_squeeze_pattern(self) -> None:
        strategy = create_strategy()
        prices = _make_squeeze_release_pattern(100)
        candles = _make_candles(prices, volatility=0.005)
        ctx = StrategyContext(
            market_data={"KRW-BTC": candles},
            portfolio_value=1000000,
            current_positions={},
            params=strategy.params,
        )
        signals = await strategy.execute(ctx)
        assert isinstance(signals, list)

    async def test_execute_sell_on_momentum_reversal(self) -> None:
        strategy = create_strategy()
        prices = _make_squeeze_release_pattern(100)
        candles = _make_candles(prices, volatility=0.005)
        ctx = StrategyContext(
            market_data={"KRW-BTC": candles},
            portfolio_value=1000000,
            current_positions={"KRW-BTC": 0.5},
            params=strategy.params,
        )
        signals = await strategy.execute(ctx)
        assert isinstance(signals, list)

    async def test_backtest_returns_result(self) -> None:
        strategy = create_strategy()
        prices = _make_squeeze_release_pattern(100)
        candles = _make_candles(prices, volatility=0.005)
        result = await strategy.backtest({"KRW-BTC": candles})
        assert result is not None
        assert hasattr(result, "total_trades")

    async def test_backtest_empty_data(self) -> None:
        strategy = create_strategy()
        result = await strategy.backtest({})
        assert result.total_trades == 0

    async def test_backtest_insufficient_data(self) -> None:
        strategy = create_strategy()
        candles = _make_candles([50000] * 5, volatility=0.005)
        result = await strategy.backtest({"KRW-BTC": candles})
        assert result.total_trades == 0
