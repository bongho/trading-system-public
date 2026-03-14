from __future__ import annotations

import pytest

from src.db.repository import StrategyRepository, TradeRepository
from src.db.schema import init_db


@pytest.fixture
async def db():
    conn = await init_db(":memory:")
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_insert_and_get_trade(db):
    repo = TradeRepository(db)
    trade_id = await repo.insert_trade(
        strategy_id="test",
        broker="upbit",
        side="buy",
        symbol="KRW-BTC",
        amount=10000,
        price=50000000,
        volume=0.0002,
        fee=5,
    )
    assert trade_id

    trades = await repo.get_recent_trades("test", limit=10)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "KRW-BTC"
    assert trades[0]["amount"] == 10000


@pytest.mark.asyncio
async def test_strategy_upsert_and_get(db):
    repo = StrategyRepository(db)
    await repo.upsert_strategy(
        id="simple_rsi",
        name="SimpleRSI",
        broker="upbit",
        symbols=["KRW-BTC"],
        capital_allocation=100000,
        params={"rsi_period": 14},
        code_path="src/strategies/simple_rsi.py",
    )

    strategy = await repo.get_strategy("simple_rsi")
    assert strategy is not None
    assert strategy["name"] == "SimpleRSI"
    assert strategy["symbols"] == ["KRW-BTC"]
    assert strategy["params"]["rsi_period"] == 14
    assert strategy["enabled"] is True


@pytest.mark.asyncio
async def test_strategy_update_capital(db):
    repo = StrategyRepository(db)
    await repo.upsert_strategy(
        id="test",
        name="Test",
        broker="upbit",
        symbols=[],
        capital_allocation=100000,
        code_path="",
    )
    await repo.update_capital("test", 95000)

    strategy = await repo.get_strategy("test")
    assert strategy["current_capital"] == 95000


@pytest.mark.asyncio
async def test_get_all_strategies(db):
    repo = StrategyRepository(db)
    for i in range(3):
        await repo.upsert_strategy(
            id=f"strat_{i}",
            name=f"Strategy {i}",
            broker="upbit",
            symbols=[],
            capital_allocation=100000,
            code_path="",
        )
    all_strats = await repo.get_all_strategies()
    assert len(all_strats) == 3
