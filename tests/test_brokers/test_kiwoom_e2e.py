"""Kiwoom 브로커 E2E 테스트 — Read-Only + 주문 (실전 API 호출).

실행 조건: .env에 KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_ACCOUNT_NO 설정
CI 제외: pytest -m "not e2e"

API 키 발급:
  1. 키움증권 오픈 API 센터: https://openapi.kiwoom.com
  2. AppKey / SecretKey 발급 (실전: api.kiwoom.com, 모의: mockapi.kiwoom.com)
  3. .env에 추가:
     KIWOOM_APP_KEY=your_app_key
     KIWOOM_APP_SECRET=your_app_secret
     KIWOOM_ACCOUNT_NO=your_account_no
     KIWOOM_IS_PAPER=false  # 실전: false, 모의: true

사용법:
    pytest tests/test_brokers/test_kiwoom_e2e.py -v              # 전체
    pytest tests/test_brokers/test_kiwoom_e2e.py -v -k "Trade"   # 주문만
    pytest tests/test_brokers/test_kiwoom_e2e.py -v -k "not Trade"  # Read-Only만

경고: TestKiwoomTrade 는 실제 자금을 사용합니다.
주의: 키움 API Rate limit — 동일 API 초당 1회. 테스트 간 1초 간격 유지.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientResponseError

from src.brokers.kiwoom import KiwoomAdapter
from src.config import settings

# loop_scope="module": 모듈 내 모든 테스트가 동일 이벤트 루프 사용
# → module-scoped adapter fixture의 aiohttp 세션과 루프 일치 보장
pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="module")]

# 테스트용 삼성전자 종목코드 (KRX 대표 종목)
_SAMSUNG = "005930"


def _require_credentials() -> KiwoomAdapter:
    if not settings.kiwoom_app_key or settings.kiwoom_app_key.startswith("your_"):
        pytest.skip("KIWOOM_APP_KEY not configured")
    if not settings.kiwoom_account_no or settings.kiwoom_account_no.startswith("your_"):
        pytest.skip("KIWOOM_ACCOUNT_NO not configured")
    return KiwoomAdapter(
        app_key=settings.kiwoom_app_key,
        app_secret=settings.kiwoom_app_secret,
        account_no=settings.kiwoom_account_no,
        is_paper=settings.kiwoom_is_paper,
    )


@pytest.fixture(scope="module")
async def adapter():
    """모듈 전체에서 adapter 인스턴스 공유 — 토큰 재발급/Rate limit 방지."""
    a = _require_credentials()
    yield a
    await a.close()


class TestKiwoomCurrentPrice:
    async def test_samsung_price_positive(self, adapter: KiwoomAdapter) -> None:
        """삼성전자 현재가 양수 확인."""
        price = await adapter.get_current_price(_SAMSUNG)
        assert price > 0, f"삼성전자 현재가 0 이하: {price}"


def _skip_on_rate_limit(exc: Exception) -> None:
    """429 Rate limit 발생 시 skip 처리 (연속 실행 후 일시 초과 가능)."""
    if isinstance(exc, ClientResponseError) and exc.status == 429:
        pytest.skip("차트 API Rate limit 초과 (429) — 충분한 간격 후 재시도 필요")


class TestKiwoomMarketData:
    async def test_minute_candles(self, adapter: KiwoomAdapter) -> None:
        """삼성전자 5분봉 조회 — 건수 + OHLCV 필드 검증."""
        await asyncio.sleep(2)  # 차트 API Rate limit 대응
        try:
            candles = await adapter.get_market_data(_SAMSUNG, interval="5m", count=10)
        except Exception as e:
            _skip_on_rate_limit(e)
            raise
        assert len(candles) > 0, "캔들 데이터 없음"
        assert len(candles) <= 10
        for c in candles:
            assert c.symbol == _SAMSUNG
            assert isinstance(c.open, float) and c.open >= 0
            assert isinstance(c.high, float) and c.high >= c.low
            assert isinstance(c.low, float) and c.low >= 0
            assert isinstance(c.close, float) and c.close >= 0
            assert isinstance(c.volume, float) and c.volume >= 0

    async def test_daily_candles(self, adapter: KiwoomAdapter) -> None:
        """삼성전자 일봉 5개 조회."""
        await asyncio.sleep(5)  # 차트 API 연속 호출 Rate limit 회피
        try:
            candles = await adapter.get_market_data(_SAMSUNG, interval="1d", count=5)
        except Exception as e:
            _skip_on_rate_limit(e)
            raise
        assert len(candles) > 0


class TestKiwoomOrderbook:
    async def test_orderbook_structure(self, adapter: KiwoomAdapter) -> None:
        """호가창 asks/bids 존재 및 price 양수 검증."""
        await asyncio.sleep(1)
        ob = await adapter.get_orderbook(_SAMSUNG)
        assert ob.symbol == _SAMSUNG
        assert len(ob.asks) > 0, "매도 호가 비어있음"
        assert len(ob.bids) > 0, "매수 호가 비어있음"
        for ask in ob.asks[:3]:
            assert float(ask["price"]) > 0
        for bid in ob.bids[:3]:
            assert float(bid["price"]) > 0

    async def test_asks_higher_than_bids(self, adapter: KiwoomAdapter) -> None:
        """최우선 매도가 > 최우선 매수가."""
        await asyncio.sleep(1)
        ob = await adapter.get_orderbook(_SAMSUNG)
        best_ask = float(ob.asks[0]["price"])
        best_bid = float(ob.bids[0]["price"])
        assert best_ask > best_bid, (
            f"스프레드 역전: ask={best_ask}, bid={best_bid}"
        )


class TestKiwoomPortfolio:
    async def test_portfolio_returns_valid_structure(self, adapter: KiwoomAdapter) -> None:
        """계좌평가현황 조회 — 잔고 구조 검증."""
        await asyncio.sleep(1)
        portfolio = await adapter.get_portfolio()
        assert portfolio.broker == "kiwoom"
        assert portfolio.total_balance >= 0
        assert portfolio.available_balance >= 0
        for item in portfolio.items:
            assert item.symbol
            assert item.balance > 0
            assert item.current_price >= 0


class TestKiwoomTrade:
    """실전 주문 E2E — 삼성전자 소량 매수 → 즉시 매도.

    주의: 실제 자금 사용.
    실행: pytest tests/test_brokers/test_kiwoom_e2e.py -v -k "Trade"
    """

    async def test_roundtrip_buy_then_sell(self, adapter: KiwoomAdapter) -> None:
        """삼성전자 1주 매수 → 즉시 매도."""
        await asyncio.sleep(1)
        price = await adapter.get_current_price(_SAMSUNG)
        assert price > 0, f"현재가 조회 실패: {price}"

        buy_result = await adapter.buy(_SAMSUNG, price * 1.05)  # 1주 + 5% 여유
        assert buy_result.success, f"매수 실패: {buy_result.error}"
        assert buy_result.order_id, "order_id 없음"
        assert buy_result.volume >= 1, f"매수 수량 0: {buy_result.volume}"

        await asyncio.sleep(2)  # 체결 대기

        sell_result = await adapter.sell(_SAMSUNG, buy_result.volume)
        assert sell_result.success, f"매도 실패: {sell_result.error}"
        assert sell_result.order_id
