"""Market Data Collector — Read-through 캐시 + 주기적 수집.

2-Layer 구조:
1. Write-through: 전략 실행 시 받은 캔들을 DB에 자동 저장
2. Read-through: 백테스트 요청 시 DB 우선 → 부족하면 API fetch → 저장 → 반환
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.brokers.base import BrokerAdapter, MarketData
from src.db.repository import MarketDataRepository

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 간격별 분 수
INTERVAL_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}


class MarketDataCollector:
    """시장 데이터 수집 및 캐시 관리.

    사용 패턴:
    1. get_candles() — 백테스트/분석용 (read-through)
    2. collect_latest() — 스케줄러에서 주기 호출 (write-through)
    3. backfill() — 과거 데이터 일괄 수집
    """

    def __init__(
        self,
        repo: MarketDataRepository,
        brokers: dict[str, BrokerAdapter],
    ) -> None:
        self._repo = repo
        self._brokers = brokers

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        days: int = 30,
        broker_name: str | None = None,
    ) -> list[MarketData]:
        """Read-through 캐시: DB에 충분하면 반환, 부족하면 API로 보충.

        Args:
            symbol: 종목 코드 (예: KRW-BTC)
            timeframe: 캔들 간격 (5m, 15m, 1h, 1d 등)
            days: 요청 기간 (일)
            broker_name: 특정 브로커 지정 (None이면 첫 번째 사용)

        Returns:
            시간순 정렬된 MarketData 리스트
        """
        mins = INTERVAL_MINUTES.get(timeframe, 5)
        needed = (days * 24 * 60) // mins
        since = (datetime.now(KST) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        # 1. DB에서 먼저 조회
        cached = await self._repo.get_candles(symbol, timeframe, since=since)

        if len(cached) >= needed * 0.9:  # 90% 이상 있으면 충분
            logger.debug(
                "Cache hit: %s %s — %d/%d candles",
                symbol, timeframe, len(cached), needed,
            )
            return cached

        # 2. 부족하면 API에서 fetch
        logger.info(
            "Cache miss: %s %s — have %d, need %d. Fetching from API...",
            symbol, timeframe, len(cached), needed,
        )
        broker = self._get_broker(broker_name, symbol)
        if not broker:
            logger.warning("No broker available for %s, returning cached data", symbol)
            return cached

        # gap-fill: DB에 있는 최신 시점 이후만 API에서 가져오기
        latest_ts = await self._repo.get_latest_timestamp(symbol, timeframe)
        if latest_ts and len(cached) > needed * 0.3:
            # 부분 fetch (gap-fill)
            fresh = await self._fetch_since(broker, symbol, timeframe, latest_ts)
        else:
            # 전체 fetch
            fresh = await self._fetch_full(broker, symbol, timeframe, days)

        if fresh:
            saved = await self._repo.upsert_candles(fresh, timeframe)
            logger.info("Saved %d candles for %s %s", saved, symbol, timeframe)

        # 3. DB에서 다시 조회 (정렬/중복 제거된 결과)
        return await self._repo.get_candles(symbol, timeframe, since=since)

    async def collect_latest(
        self,
        symbol: str,
        timeframe: str = "5m",
        broker_name: str | None = None,
    ) -> list[MarketData]:
        """Write-through: 최신 캔들 200개를 가져와서 DB에 저장.

        스케줄러에서 전략 실행 시 호출.
        """
        broker = self._get_broker(broker_name, symbol)
        if not broker:
            return []

        candles = await broker.get_market_data(symbol, timeframe, 200)
        if candles:
            await self._repo.upsert_candles(candles, timeframe)
            logger.debug(
                "Collected %d candles for %s %s", len(candles), symbol, timeframe
            )
        return candles

    async def backfill(
        self,
        symbol: str,
        timeframe: str = "5m",
        days: int = 90,
        broker_name: str | None = None,
    ) -> int:
        """과거 데이터 일괄 수집 (초기 세팅용).

        Returns:
            저장된 캔들 수
        """
        broker = self._get_broker(broker_name, symbol)
        if not broker:
            return 0

        if not hasattr(broker, "get_historical_data"):
            logger.warning("Broker %s does not support historical data", broker.name)
            return 0

        logger.info("Backfilling %s %s for %d days...", symbol, timeframe, days)
        candles = await broker.get_historical_data(symbol, timeframe, days)
        if candles:
            saved = await self._repo.upsert_candles(candles, timeframe)
            logger.info("Backfill complete: %d candles for %s", saved, symbol)
            return saved
        return 0

    async def collect_all_strategies(
        self,
        symbols_by_broker: dict[str, list[str]],
        timeframe: str = "5m",
    ) -> int:
        """모든 전략의 심볼에 대해 최신 데이터 수집.

        스케줄러 cron job에서 호출.
        """
        total = 0
        for broker_name, symbols in symbols_by_broker.items():
            for symbol in symbols:
                candles = await self.collect_latest(symbol, timeframe, broker_name)
                total += len(candles)
        return total

    def _get_broker(
        self, broker_name: str | None, symbol: str
    ) -> BrokerAdapter | None:
        """브로커 결정: 명시적 지정 > 심볼 기반 추론 > 첫 번째."""
        if broker_name and broker_name in self._brokers:
            return self._brokers[broker_name]

        # KRW- 시작 심볼 → upbit, 숫자 코드 → kiwoom
        if symbol.startswith("KRW-") and "upbit" in self._brokers:
            return self._brokers["upbit"]
        if symbol[0].isdigit() and "kiwoom" in self._brokers:
            return self._brokers["kiwoom"]

        # fallback: 첫 번째 브로커
        if self._brokers:
            return next(iter(self._brokers.values()))
        return None

    async def _fetch_full(
        self,
        broker: BrokerAdapter,
        symbol: str,
        timeframe: str,
        days: int,
    ) -> list[MarketData]:
        """전체 기간 데이터 API fetch."""
        if hasattr(broker, "get_historical_data"):
            return await broker.get_historical_data(symbol, timeframe, days)
        # historical_data 미지원 브로커는 최근 200개만
        return await broker.get_market_data(symbol, timeframe, 200)

    async def _fetch_since(
        self,
        broker: BrokerAdapter,
        symbol: str,
        timeframe: str,
        since_ts: str,
    ) -> list[MarketData]:
        """특정 시점 이후 데이터만 fetch (gap-fill)."""
        # gap 크기 계산
        try:
            since_dt = datetime.fromisoformat(since_ts)
        except ValueError:
            since_dt = datetime.strptime(since_ts, "%Y-%m-%dT%H:%M:%S")

        now = datetime.now(KST)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=KST)

        gap_minutes = (now - since_dt).total_seconds() / 60
        mins = INTERVAL_MINUTES.get(timeframe, 5)
        gap_candles = int(gap_minutes / mins)

        if gap_candles <= 200:
            # 단일 요청으로 충분
            return await broker.get_market_data(
                symbol, timeframe, min(gap_candles + 10, 200)
            )

        # gap이 크면 historical로 fetch
        gap_days = max(1, int(gap_minutes / (24 * 60)) + 1)
        if hasattr(broker, "get_historical_data"):
            return await broker.get_historical_data(symbol, timeframe, gap_days)
        return await broker.get_market_data(symbol, timeframe, 200)
