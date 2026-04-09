from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from math import floor
from typing import Any

import aiohttp

from src.brokers.base import (
    BrokerAdapter,
    MarketData,
    Orderbook,
    Portfolio,
    PortfolioItem,
    TradeResult,
)

logger = logging.getLogger(__name__)

_MINUTE_INTERVALS = {"1m", "3m", "5m", "10m", "15m", "30m", "60m"}
_INTERVAL_TO_TIC = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "30m": "30",
    "60m": "60",
}


class KiwoomAdapter(BrokerAdapter):
    """키움증권 REST API 브로커 어댑터.

    공식 문서: https://api.kiwoom.com
    모의투자: https://mockapi.kiwoom.com (KRX 전용)
    """

    name = "kiwoom"
    _BASE_URL = "https://api.kiwoom.com"
    _MOCK_URL = "https://mockapi.kiwoom.com"
    _TOKEN_TTL = timedelta(hours=24)
    _TOKEN_REFRESH_BUFFER = timedelta(minutes=5)

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        is_paper: bool = False,
        **_: Any,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._is_paper = is_paper
        self._base_url = self._MOCK_URL if is_paper else self._BASE_URL

        self._token: str = ""
        self._token_expires_at: datetime = datetime.min
        self._token_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_token(self) -> None:
        """토큰이 없거나 만료 임박이면 재발급."""
        if datetime.utcnow() < self._token_expires_at - self._TOKEN_REFRESH_BUFFER:
            return
        async with self._token_lock:
            if datetime.utcnow() < self._token_expires_at - self._TOKEN_REFRESH_BUFFER:
                return
            url = f"{self._base_url}/oauth2/token"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._app_secret,
            }
            async with self._get_session().post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
            if data.get("return_code", -1) != 0:
                raise RuntimeError(f"Kiwoom token error: {data.get('return_msg')}")
            self._token = data["token"]
            self._token_expires_at = datetime.utcnow() + self._TOKEN_TTL
            logger.debug("Kiwoom token refreshed")

    async def _request(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        cont_yn: str = "N",
        next_key: str = "",
    ) -> dict[str, Any]:
        """단일 POST 요청."""
        await self._ensure_token()
        url = f"{self._base_url}{path}"
        headers = {
            "api-id": api_id,
            "authorization": f"Bearer {self._token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "Content-Type": "application/json",
        }
        async with self._get_session().post(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data.get("return_code", -1) != 0:
            raise RuntimeError(
                f"Kiwoom API error [{api_id}]: {data.get('return_msg', data)}"
            )
        return data

    async def _request_paginated(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        list_key: str,
        count: int,
    ) -> list[dict[str, Any]]:
        """cont-yn 페이지네이션으로 count개 이상 수집."""
        results: list[dict[str, Any]] = []
        cont_yn = "N"
        next_key = ""

        while len(results) < count:
            await self._ensure_token()
            url = f"{self._base_url}{path}"
            headers = {
                "api-id": api_id,
                "authorization": f"Bearer {self._token}",
                "cont-yn": cont_yn,
                "next-key": next_key,
                "appkey": self._app_key,
                "appsecret": self._app_secret,
                "Content-Type": "application/json",
            }
            async with self._get_session().post(
                url, headers=headers, json=body
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                resp_cont_yn = resp.headers.get("cont-yn", "N")
                resp_next_key = resp.headers.get("next-key", "")

            if data.get("return_code", -1) != 0:
                raise RuntimeError(
                    f"Kiwoom API error [{api_id}]: {data.get('return_msg', data)}"
                )

            page_items: list[dict[str, Any]] = data.get(list_key, [])
            results.extend(page_items)

            if resp_cont_yn != "Y" or not resp_next_key:
                break
            cont_yn = "Y"
            next_key = resp_next_key

        return results[:count]

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return default

    # ------------------------------------------------------------------
    # BrokerAdapter implementation
    # ------------------------------------------------------------------

    async def buy(self, symbol: str, amount: float) -> TradeResult:
        """시장가 매수 (amount: KRW 금액)."""
        try:
            price = await self.get_current_price(symbol)
            if price <= 0:
                return TradeResult(success=False, symbol=symbol, side="buy", error="현재가 조회 실패")
            qty = floor(amount / price)
            if qty <= 0:
                return TradeResult(
                    success=False, symbol=symbol, side="buy",
                    error=f"수량 부족: {amount}원 / {price}원",
                )
            data = await self._request(
                api_id="kt10000",
                path="/api/dostk/ordr",
                body={
                    "dmst_stex_tp": "KRX",
                    "stk_cd": symbol,
                    "ord_qty": str(qty),
                    "trde_tp": "03",  # 시장가
                    "ord_uv": "0",
                },
            )
            return TradeResult(
                success=True,
                order_id=str(data.get("ord_no", "")),
                symbol=symbol,
                side="buy",
                price=price,
                volume=float(qty),
                amount=price * qty,
            )
        except Exception as exc:
            logger.exception("Kiwoom buy failed: %s", exc)
            return TradeResult(success=False, symbol=symbol, side="buy", error=str(exc))

    async def sell(self, symbol: str, volume: float) -> TradeResult:
        """시장가 매도 (volume: 주식 수량)."""
        try:
            qty = int(volume)
            if qty <= 0:
                return TradeResult(success=False, symbol=symbol, side="sell", error="수량 0")
            price = await self.get_current_price(symbol)
            data = await self._request(
                api_id="kt10001",
                path="/api/dostk/ordr",
                body={
                    "dmst_stex_tp": "KRX",
                    "stk_cd": symbol,
                    "ord_qty": str(qty),
                    "trde_tp": "03",  # 시장가
                    "ord_uv": "0",
                },
            )
            return TradeResult(
                success=True,
                order_id=str(data.get("ord_no", "")),
                symbol=symbol,
                side="sell",
                price=price,
                volume=float(qty),
                amount=price * qty,
            )
        except Exception as exc:
            logger.exception("Kiwoom sell failed: %s", exc)
            return TradeResult(success=False, symbol=symbol, side="sell", error=str(exc))

    async def get_portfolio(self) -> Portfolio:
        """계좌평가현황 조회 (kt00004)."""
        data = await self._request(
            api_id="kt00004",
            path="/api/dostk/acnt",
            body={"qry_tp": "0", "dmst_stex_tp": "KRX"},
        )
        available = self._to_float(data.get("entr", 0))
        total = self._to_float(data.get("aset_evlt_amt", available))

        items: list[PortfolioItem] = []
        for row in data.get("stk_acnt_evlt_prst", []):
            balance = self._to_float(row.get("rmnd_qty", 0))
            if balance <= 0:
                continue
            items.append(
                PortfolioItem(
                    symbol=str(row.get("stk_cd", "")),
                    balance=balance,
                    avg_buy_price=self._to_float(row.get("avg_prc", 0)),
                    current_price=self._to_float(row.get("cur_prc", 0)),
                )
            )
        return Portfolio(
            broker=self.name,
            total_balance=total,
            available_balance=available,
            items=items,
        )

    async def get_market_data(
        self, symbol: str, interval: str = "5m", count: int = 200
    ) -> list[MarketData]:
        """OHLCV 조회. 분봉(ka10080) / 일봉(ka10081)."""
        if interval in _MINUTE_INTERVALS:
            api_id = "ka10080"
            body: dict[str, Any] = {
                "stk_cd": symbol,
                "tic_scope": _INTERVAL_TO_TIC.get(interval, "5"),
                "base_dt": "",
                "upd_stkpc_tp": "1",
            }
        else:
            api_id = "ka10081"
            body = {
                "stk_cd": symbol,
                "base_dt": "",
                "upd_stkpc_tp": "1",
            }

        rows = await self._request_paginated(
            api_id=api_id,
            path="/api/dostk/chart",
            body=body,
            list_key="stk_chart_qry",
            count=count,
        )

        return [
            MarketData(
                symbol=symbol,
                timestamp=str(row.get("date", "")),
                open=self._to_float(row.get("open_pric", 0)),
                high=self._to_float(row.get("high_pric", 0)),
                low=self._to_float(row.get("low_pric", 0)),
                close=self._to_float(row.get("close_pric", 0)),
                volume=self._to_float(row.get("trde_qty", 0)),
            )
            for row in rows
        ]

    async def get_orderbook(self, symbol: str) -> Orderbook:
        """호가창 조회 (ka10004).

        응답 필드:
          매도: sel_fpr_bid / sel_fpr_req  ~  sel_10th_pre_bid / sel_10th_pre_req
          매수: buy_fpr_bid / buy_fpr_req  ~  buy_10th_pre_bid / buy_10th_pre_req
        """
        data = await self._request(
            api_id="ka10004",
            path="/api/dostk/mrkcond",
            body={"stk_cd": symbol},
        )

        ask_price_keys = [
            "sel_fpr_bid", "sel_2nd_pre_bid", "sel_3rd_pre_bid", "sel_4th_pre_bid",
            "sel_5th_pre_bid", "sel_6th_pre_bid", "sel_7th_pre_bid", "sel_8th_pre_bid",
            "sel_9th_pre_bid", "sel_10th_pre_bid",
        ]
        ask_vol_keys = [
            "sel_fpr_req", "sel_2nd_pre_req", "sel_3rd_pre_req", "sel_4th_pre_req",
            "sel_5th_pre_req", "sel_6th_pre_req", "sel_7th_pre_req", "sel_8th_pre_req",
            "sel_9th_pre_req", "sel_10th_pre_req",
        ]
        bid_price_keys = [
            "buy_fpr_bid", "buy_2nd_pre_bid", "buy_3rd_pre_bid", "buy_4th_pre_bid",
            "buy_5th_pre_bid", "buy_6th_pre_bid", "buy_7th_pre_bid", "buy_8th_pre_bid",
            "buy_9th_pre_bid", "buy_10th_pre_bid",
        ]
        bid_vol_keys = [
            "buy_fpr_req", "buy_2nd_pre_req", "buy_3rd_pre_req", "buy_4th_pre_req",
            "buy_5th_pre_req", "buy_6th_pre_req", "buy_7th_pre_req", "buy_8th_pre_req",
            "buy_9th_pre_req", "buy_10th_pre_req",
        ]

        asks = [
            {"price": self._to_float(data.get(pk, 0)), "volume": self._to_float(data.get(vk, 0))}
            for pk, vk in zip(ask_price_keys, ask_vol_keys)
            if self._to_float(data.get(pk, 0)) > 0
        ]
        bids = [
            {"price": self._to_float(data.get(pk, 0)), "volume": self._to_float(data.get(vk, 0))}
            for pk, vk in zip(bid_price_keys, bid_vol_keys)
            if self._to_float(data.get(pk, 0)) > 0
        ]

        return Orderbook(symbol=symbol, asks=asks, bids=bids)

    async def get_current_price(self, symbol: str) -> float:
        """현재가 조회 (ka10007)."""
        data = await self._request(
            api_id="ka10007",
            path="/api/dostk/mrkcond",
            body={"stk_cd": symbol},
        )
        return self._to_float(data.get("cur_prc", 0))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
