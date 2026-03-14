from __future__ import annotations

import pytest

from src.brokers.base import MarketData
from src.strategies.base import StrategyContext
from src.strategies.simple_rsi import create_strategy


def _make_market_data(prices: list[float]) -> list[MarketData]:
    """가격 리스트로 MarketData 생성 (테스트용)"""
    return [
        MarketData(
            symbol="KRW-BTC",
            timestamp=f"2026-03-01T{i:02d}:00:00",
            open=p,
            high=p * 1.01,
            low=p * 0.99,
            close=p,
            volume=1000.0,
        )
        for i, p in enumerate(prices)
    ]


@pytest.mark.asyncio
async def test_simple_rsi_creation():
    strategy = create_strategy(capital_allocation=100000)
    assert strategy.id == "simple_rsi"
    assert strategy.name == "SimpleRSI"
    assert strategy.broker == "upbit"
    assert strategy.params["rsi_period"] == 14


@pytest.mark.asyncio
async def test_simple_rsi_no_signal_with_insufficient_data():
    strategy = create_strategy()
    # RSI는 최소 period+1 캔들 필요
    prices = [50000.0] * 5  # 5개로는 부족 (period=14)
    data = _make_market_data(prices)

    ctx = StrategyContext(
        market_data={"KRW-BTC": data},
        portfolio_value=100000,
        current_positions={},
        params=strategy.params,
    )
    signals = await strategy.execute(ctx)
    assert signals == []


@pytest.mark.asyncio
async def test_simple_rsi_default_params():
    strategy = create_strategy()
    assert strategy.params["buy_threshold"] == 30
    assert strategy.params["sell_threshold"] == 70
    assert strategy.params["stop_loss_pct"] == 0.02


@pytest.mark.asyncio
async def test_simple_rsi_param_update():
    strategy = create_strategy()
    strategy.update_params({"rsi_period": 21})
    assert strategy.params["rsi_period"] == 21
    assert strategy.params["buy_threshold"] == 30  # unchanged
