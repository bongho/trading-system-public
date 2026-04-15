"""Upbit 브로커 E2E 테스트 — Read-Only + 주문 (실제 API 호출).

실행 조건: .env에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 설정
CI 제외: pytest -m "not e2e"

사용법:
    pytest tests/test_brokers/test_upbit_e2e.py -v              # 전체
    pytest tests/test_brokers/test_upbit_e2e.py -v -k "Trade"   # 주문만
    pytest tests/test_brokers/test_upbit_e2e.py -v -k "not Trade"  # Read-Only만

경고: TestUpbitOrder 는 실제 자금(~5,000 KRW + 스프레드)을 사용합니다.
"""

from __future__ import annotations

import asyncio

import pytest

from src.brokers.upbit import UpbitAdapter
from src.config import settings

pytestmark = pytest.mark.e2e


def _require_credentials() -> UpbitAdapter:
    if not settings.upbit_access_key:
        pytest.skip("UPBIT_ACCESS_KEY not configured")
    return UpbitAdapter(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
    )


class TestUpbitCurrentPrice:
    async def test_btc_price_positive(self) -> None:
        adapter = _require_credentials()
        try:
            price = await adapter.get_current_price("KRW-BTC")
            assert price > 0, f"BTC 현재가가 0 이하: {price}"
        finally:
            await adapter.close()

    async def test_eth_price_positive(self) -> None:
        adapter = _require_credentials()
        try:
            price = await adapter.get_current_price("KRW-ETH")
            assert price > 0
        finally:
            await adapter.close()


class TestUpbitMarketData:
    async def test_candles_count(self) -> None:
        """5분봉 10개 요청 → 정확히 10개 반환."""
        adapter = _require_credentials()
        try:
            candles = await adapter.get_market_data("KRW-BTC", interval="5m", count=10)
            assert len(candles) == 10
        finally:
            await adapter.close()

    async def test_candle_fields(self) -> None:
        """캔들 필드 타입 및 OHLCV 유효성 검증."""
        adapter = _require_credentials()
        try:
            candles = await adapter.get_market_data("KRW-BTC", interval="5m", count=3)
            assert len(candles) > 0
            for c in candles:
                assert c.symbol == "KRW-BTC"
                assert isinstance(c.open, float) and c.open > 0
                assert isinstance(c.high, float) and c.high >= c.low
                assert isinstance(c.low, float) and c.low > 0
                assert isinstance(c.close, float) and c.close > 0
                assert isinstance(c.volume, float) and c.volume >= 0
                assert c.timestamp  # 빈 문자열 아님
        finally:
            await adapter.close()

    async def test_candles_sorted_oldest_first(self) -> None:
        """캔들이 오래된 순(ascending)으로 정렬되어 있는지 확인."""
        adapter = _require_credentials()
        try:
            candles = await adapter.get_market_data("KRW-BTC", interval="5m", count=5)
            timestamps = [c.timestamp for c in candles]
            assert timestamps == sorted(timestamps), "캔들이 시간 오름차순이 아님"
        finally:
            await adapter.close()

    async def test_daily_candles(self) -> None:
        """일봉 데이터 조회."""
        adapter = _require_credentials()
        try:
            candles = await adapter.get_market_data("KRW-BTC", interval="1d", count=7)
            assert len(candles) == 7
        finally:
            await adapter.close()


class TestUpbitOrderbook:
    async def test_orderbook_structure(self) -> None:
        """호가창 asks/bids 존재 및 price 양수 검증."""
        adapter = _require_credentials()
        try:
            ob = await adapter.get_orderbook("KRW-BTC")
            assert ob.symbol == "KRW-BTC"
            assert len(ob.asks) > 0, "매도 호가 비어있음"
            assert len(ob.bids) > 0, "매수 호가 비어있음"
            for ask in ob.asks[:3]:
                assert float(ask["price"]) > 0
            for bid in ob.bids[:3]:
                assert float(bid["price"]) > 0
        finally:
            await adapter.close()

    async def test_asks_higher_than_bids(self) -> None:
        """최우선 매도가 > 최우선 매수가."""
        adapter = _require_credentials()
        try:
            ob = await adapter.get_orderbook("KRW-BTC")
            best_ask = float(ob.asks[0]["price"])
            best_bid = float(ob.bids[0]["price"])
            assert best_ask > best_bid, (
                f"스프레드 역전: ask={best_ask}, bid={best_bid}"
            )
        finally:
            await adapter.close()


class TestUpbitPortfolio:
    async def test_portfolio_returns_valid_structure(self) -> None:
        """포트폴리오 조회 — 잔고 구조 검증 (금액 노출 없이)."""
        adapter = _require_credentials()
        try:
            portfolio = await adapter.get_portfolio()
            assert portfolio.broker == "upbit"
            assert portfolio.total_balance >= 0
            assert portfolio.available_balance >= 0
            assert portfolio.available_balance <= portfolio.total_balance
            # 보유 종목이 있다면 필드 검증
            for item in portfolio.items:
                assert item.symbol.startswith("KRW-")
                assert item.balance > 0
                # 상장폐지 종목은 current_price=0.0(fallback) 가능
                assert item.current_price >= 0
        finally:
            await adapter.close()


class TestUpbitTrade:
    """실제 주문 E2E — 소액(5,000 KRW) 매수 → 체결 대기 → 전량 매도.

    주의: 실제 자금(~10,000 KRW + 스프레드)을 사용합니다.
    실행: pytest tests/test_brokers/test_upbit_e2e.py -v -k "Trade"
    """

    async def test_roundtrip_buy_then_sell(self) -> None:
        """KRW-BTC 10,000 KRW 매수 → 증가분만 매도 (기존 보유 BTC 보존).

        매수 전 BTC 잔고를 기록하고, 매수 후 증가한 수량만 매도합니다.
        기존에 BTC를 보유하고 있어도 기존 보유분은 건드리지 않습니다.
        10,000 KRW 매수: 수수료+스프레드 차감 후에도 매도 최소금액(5,000 KRW) 초과 보장.
        """
        adapter = _require_credentials()
        try:
            # 1. 매수 전 BTC 잔고 기록 (기존 보유분 보호)
            before_portfolio = await adapter.get_portfolio()
            before_btc = next(
                (i for i in before_portfolio.items if i.symbol == "KRW-BTC"), None
            )
            before_balance = before_btc.balance if before_btc else 0.0

            # 2. 소액 시장가 매수 (10,000 KRW — 매도 최소금액 5,000 KRW 여유 확보)
            buy_result = await adapter.buy("KRW-BTC", 10_000)
            assert buy_result.success, f"매수 실패: {buy_result.error}"
            assert buy_result.order_id, "order_id 없음 — 주문 미접수"

            # 3. 체결 대기 (시장가 매수는 보통 1초 내 체결)
            await asyncio.sleep(2)

            # 4. 매수 후 포트폴리오 조회 → 증가분 계산
            #    buy() 반환의 executed_volume은 0일 수 있음 (Upbit 비동기 체결)
            after_portfolio = await adapter.get_portfolio()
            after_btc = next(
                (i for i in after_portfolio.items if i.symbol == "KRW-BTC"), None
            )
            assert after_btc is not None, "매수 후 KRW-BTC 잔고 없음 — 미체결 확인 필요"

            bought_volume = after_btc.balance - before_balance
            assert bought_volume > 0, (
                f"매수 후 BTC 증가분 없음 "
                f"(before={before_balance}, after={after_btc.balance})"
            )

            # 5. 증가분만 시장가 매도 (기존 보유분 보존)
            sell_result = await adapter.sell("KRW-BTC", bought_volume)
            assert sell_result.success, f"매도 실패: {sell_result.error}"
            assert sell_result.order_id
        finally:
            await adapter.close()
