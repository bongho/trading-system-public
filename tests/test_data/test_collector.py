"""MarketDataCollector 테스트 — mock broker + real DB."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from src.brokers.base import MarketData
from src.data.collector import MarketDataCollector
from src.db.repository import MarketDataRepository
from src.db.schema import SCHEMA_SQL


def _candle(symbol: str, ts: str, close: float) -> MarketData:
    return MarketData(
        symbol=symbol, timestamp=ts,
        open=close - 1, high=close + 1, low=close - 2,
        close=close, volume=100.0,
    )


def _make_candles(symbol: str, count: int, base_close: float = 90000000) -> list[MarketData]:
    return [
        _candle(symbol, f"2026-03-{(i // 288 + 1):02d}T{(i % 288 * 5 // 60):02d}:{(i % 288 * 5 % 60):02d}:00", base_close + i * 1000)
        for i in range(count)
    ]


@pytest.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = await aiosqlite.connect(db_path)
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    yield MarketDataRepository(db)
    await db.close()


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.name = "upbit"
    broker.get_market_data = AsyncMock(return_value=_make_candles("KRW-BTC", 200))
    broker.get_historical_data = AsyncMock(return_value=_make_candles("KRW-BTC", 500))
    return broker


class TestCollector:
    @pytest.mark.asyncio
    async def test_collect_latest_saves_to_db(
        self, repo: MarketDataRepository, mock_broker
    ) -> None:
        collector = MarketDataCollector(repo=repo, brokers={"upbit": mock_broker})
        candles = await collector.collect_latest("KRW-BTC", "5m", "upbit")

        assert len(candles) == 200
        # DB에 저장되었는지 확인
        stored = await repo.get_candle_count("KRW-BTC", "5m")
        assert stored == 200

    @pytest.mark.asyncio
    async def test_get_candles_cache_hit(
        self, repo: MarketDataRepository, mock_broker
    ) -> None:
        """DB에 충분한 데이터가 있으면 API 호출하지 않음."""
        # 미리 DB에 데이터 채우기
        candles = _make_candles("KRW-BTC", 9000)
        await repo.upsert_candles(candles, "5m")

        collector = MarketDataCollector(repo=repo, brokers={"upbit": mock_broker})
        result = await collector.get_candles("KRW-BTC", "5m", days=30)

        # API 호출 없이 캐시에서 반환
        assert len(result) > 0
        mock_broker.get_historical_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_candles_cache_miss_fetches_api(
        self, repo: MarketDataRepository, mock_broker
    ) -> None:
        """DB에 데이터가 없으면 API에서 fetch."""
        collector = MarketDataCollector(repo=repo, brokers={"upbit": mock_broker})
        result = await collector.get_candles("KRW-BTC", "5m", days=30)

        # API가 호출되었어야 함
        assert mock_broker.get_historical_data.called or mock_broker.get_market_data.called

    @pytest.mark.asyncio
    async def test_backfill(
        self, repo: MarketDataRepository, mock_broker
    ) -> None:
        collector = MarketDataCollector(repo=repo, brokers={"upbit": mock_broker})
        saved = await collector.backfill("KRW-BTC", "5m", days=90, broker_name="upbit")

        assert saved == 500  # mock returns 500 candles
        stored = await repo.get_candle_count("KRW-BTC", "5m")
        assert stored == 500

    @pytest.mark.asyncio
    async def test_broker_resolution_by_symbol(
        self, repo: MarketDataRepository
    ) -> None:
        """KRW- 심볼은 upbit, 숫자 심볼은 kiwoom."""
        upbit = AsyncMock()
        upbit.name = "upbit"
        upbit.get_market_data = AsyncMock(return_value=[])
        kiwoom = AsyncMock()
        kiwoom.name = "kiwoom"
        kiwoom.get_market_data = AsyncMock(return_value=[])

        collector = MarketDataCollector(
            repo=repo, brokers={"upbit": upbit, "kiwoom": kiwoom}
        )

        await collector.collect_latest("KRW-BTC", "5m")
        upbit.get_market_data.assert_called_once()
        kiwoom.get_market_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_broker_returns_empty(
        self, repo: MarketDataRepository
    ) -> None:
        collector = MarketDataCollector(repo=repo, brokers={})
        result = await collector.collect_latest("KRW-BTC", "5m")
        assert result == []
