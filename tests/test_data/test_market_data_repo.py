"""MarketDataRepository 테스트."""

from __future__ import annotations

import pytest

import aiosqlite

from src.brokers.base import MarketData
from src.db.repository import MarketDataRepository
from src.db.schema import SCHEMA_SQL


@pytest.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = await aiosqlite.connect(db_path)
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    yield MarketDataRepository(db)
    await db.close()


def _candle(symbol: str, ts: str, close: float) -> MarketData:
    return MarketData(
        symbol=symbol,
        timestamp=ts,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
    )


class TestMarketDataRepository:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, repo: MarketDataRepository) -> None:
        candles = [
            _candle("KRW-BTC", "2026-03-15T09:00:00", 90000000),
            _candle("KRW-BTC", "2026-03-15T09:05:00", 90100000),
            _candle("KRW-BTC", "2026-03-15T09:10:00", 90200000),
        ]
        saved = await repo.upsert_candles(candles, "5m")
        assert saved == 3

        result = await repo.get_candles("KRW-BTC", "5m")
        assert len(result) == 3
        assert result[0].timestamp == "2026-03-15T09:00:00"
        assert result[2].close == 90200000

    @pytest.mark.asyncio
    async def test_upsert_deduplication(self, repo: MarketDataRepository) -> None:
        candle = _candle("KRW-BTC", "2026-03-15T09:00:00", 90000000)
        await repo.upsert_candles([candle], "5m")

        # 같은 타임스탬프에 다른 값으로 업데이트
        updated = _candle("KRW-BTC", "2026-03-15T09:00:00", 91000000)
        await repo.upsert_candles([updated], "5m")

        result = await repo.get_candles("KRW-BTC", "5m")
        assert len(result) == 1
        assert result[0].close == 91000000  # 업데이트됨

    @pytest.mark.asyncio
    async def test_get_candles_with_range(self, repo: MarketDataRepository) -> None:
        candles = [
            _candle("KRW-BTC", f"2026-03-15T{h:02d}:00:00", 90000000 + h * 100000)
            for h in range(10)
        ]
        await repo.upsert_candles(candles, "1h")

        result = await repo.get_candles(
            "KRW-BTC", "1h",
            since="2026-03-15T03:00:00",
            until="2026-03-15T06:00:00",
        )
        assert len(result) == 4  # 03, 04, 05, 06

    @pytest.mark.asyncio
    async def test_get_candles_with_limit(self, repo: MarketDataRepository) -> None:
        candles = [
            _candle("KRW-BTC", f"2026-03-15T{h:02d}:00:00", 90000000)
            for h in range(10)
        ]
        await repo.upsert_candles(candles, "1h")

        result = await repo.get_candles("KRW-BTC", "1h", limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_candle_count(self, repo: MarketDataRepository) -> None:
        candles = [
            _candle("KRW-BTC", f"2026-03-15T{h:02d}:00:00", 90000000)
            for h in range(5)
        ]
        await repo.upsert_candles(candles, "5m")

        count = await repo.get_candle_count("KRW-BTC", "5m")
        assert count == 5
        # 다른 심볼은 0
        assert await repo.get_candle_count("KRW-ETH", "5m") == 0

    @pytest.mark.asyncio
    async def test_latest_oldest_timestamp(self, repo: MarketDataRepository) -> None:
        candles = [
            _candle("KRW-BTC", "2026-03-15T09:00:00", 90000000),
            _candle("KRW-BTC", "2026-03-15T12:00:00", 91000000),
            _candle("KRW-BTC", "2026-03-15T15:00:00", 92000000),
        ]
        await repo.upsert_candles(candles, "1h")

        assert await repo.get_latest_timestamp("KRW-BTC", "1h") == "2026-03-15T15:00:00"
        assert await repo.get_oldest_timestamp("KRW-BTC", "1h") == "2026-03-15T09:00:00"

    @pytest.mark.asyncio
    async def test_empty_returns_none(self, repo: MarketDataRepository) -> None:
        assert await repo.get_latest_timestamp("KRW-BTC", "5m") is None
        assert await repo.get_oldest_timestamp("KRW-BTC", "5m") is None
        assert await repo.get_candle_count("KRW-BTC", "5m") == 0

    @pytest.mark.asyncio
    async def test_symbol_isolation(self, repo: MarketDataRepository) -> None:
        """서로 다른 심볼의 데이터가 섞이지 않는지 확인."""
        await repo.upsert_candles(
            [_candle("KRW-BTC", "2026-03-15T09:00:00", 90000000)], "5m"
        )
        await repo.upsert_candles(
            [_candle("KRW-ETH", "2026-03-15T09:00:00", 4000000)], "5m"
        )

        btc = await repo.get_candles("KRW-BTC", "5m")
        eth = await repo.get_candles("KRW-ETH", "5m")
        assert len(btc) == 1
        assert len(eth) == 1
        assert btc[0].close == 90000000
        assert eth[0].close == 4000000

    @pytest.mark.asyncio
    async def test_timeframe_isolation(self, repo: MarketDataRepository) -> None:
        """같은 심볼이라도 timeframe이 다르면 분리."""
        await repo.upsert_candles(
            [_candle("KRW-BTC", "2026-03-15T09:00:00", 90000000)], "5m"
        )
        await repo.upsert_candles(
            [_candle("KRW-BTC", "2026-03-15T09:00:00", 90500000)], "1h"
        )

        m5 = await repo.get_candles("KRW-BTC", "5m")
        h1 = await repo.get_candles("KRW-BTC", "1h")
        assert m5[0].close == 90000000
        assert h1[0].close == 90500000
