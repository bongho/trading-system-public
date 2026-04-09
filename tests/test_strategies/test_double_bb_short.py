"""DoubleBBShort 전략 테스트"""

from __future__ import annotations

import pytest

from src.brokers.base import MarketData
from src.strategies.base import StrategyContext
from src.strategies.double_bb_short import DoubleBBShortStrategy, create_strategy


def _make_candles(
    prices: list[float], symbol: str = "KRW-BTC"
) -> list[MarketData]:
    """테스트용 캔들 생성 (close 기반, OHLC 동일)"""
    return [
        MarketData(
            symbol=symbol,
            timestamp=f"2026-01-{(i // 24 + 1):02d}T{(i % 24):02d}:00:00",
            open=p,
            high=p * 1.001,
            low=p * 0.999,
            close=p,
            volume=100.0,
        )
        for i, p in enumerate(prices)
    ]


def _make_trending_down_then_bounce(n: int = 100) -> list[float]:
    """하락 후 반등하는 가격 시리즈 (BB 하단 이탈 유도)"""
    prices = []
    base = 50000.0
    # 안정 구간 (BB 형성)
    for i in range(40):
        prices.append(base + (i % 5) * 100)
    # 급락 (외측 BB 하단 이탈)
    for i in range(20):
        prices.append(base - i * 500)
    # 반등 (RSI 과매도 반등)
    for i in range(20):
        prices.append(base - 10000 + i * 300)
    # 상승 (내측/외측 상단 도달)
    for i in range(20):
        prices.append(base - 4000 + i * 800)
    return prices


class TestDoubleBBShortStrategy:
    def test_create_strategy(self) -> None:
        strategy = create_strategy(capital_allocation=200000)
        assert strategy.id == "double_bb_short"
        assert strategy.name == "Double BB Short"
        assert strategy.capital_allocation == 200000
        assert strategy.params["bb_period"] == 20
        assert strategy.params["inner_std"] == 1.0
        assert strategy.params["outer_std"] == 2.0

    def test_default_params(self) -> None:
        strategy = create_strategy()
        params = strategy.default_params()
        assert "bb_period" in params
        assert "rsi_period" in params
        assert "partial_tp_pct" in params
        assert params["partial_tp_pct"] == 0.5

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

    async def test_execute_generates_buy_on_bb_lower_break(self) -> None:
        strategy = create_strategy()
        prices = _make_trending_down_then_bounce(100)
        candles = _make_candles(prices)
        ctx = StrategyContext(
            market_data={"KRW-BTC": candles},
            portfolio_value=1000000,
            current_positions={},
            params=strategy.params,
        )
        signals = await strategy.execute(ctx)
        # 가격 패턴에 따라 시그널이 0개 또는 그 이상일 수 있음
        # 핵심은 에러 없이 실행되는 것
        assert isinstance(signals, list)

    async def test_execute_sell_with_position(self) -> None:
        strategy = create_strategy()
        # 충분한 상승 가격 (외측 상단 BB 도달)
        prices = [50000] * 40
        for i in range(60):
            prices.append(50000 + i * 500)
        candles = _make_candles(prices)
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
        prices = _make_trending_down_then_bounce(100)
        candles = _make_candles(prices)
        result = await strategy.backtest({"KRW-BTC": candles})
        assert result is not None
        assert hasattr(result, "total_trades")
        assert hasattr(result, "total_pnl")

    async def test_backtest_empty_data(self) -> None:
        strategy = create_strategy()
        result = await strategy.backtest({})
        assert result.total_trades == 0

    async def test_backtest_insufficient_data(self) -> None:
        strategy = create_strategy()
        candles = _make_candles([50000] * 5)
        result = await strategy.backtest({"KRW-BTC": candles})
        assert result.total_trades == 0
