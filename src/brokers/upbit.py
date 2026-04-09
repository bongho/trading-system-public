from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any
from urllib.parse import urlencode

import aiohttp
import jwt

from src.brokers.base import (
    BrokerAdapter,
    MarketData,
    Orderbook,
    Portfolio,
    PortfolioItem,
    TradeResult,
)

logger = logging.getLogger(__name__)

UPBIT_API_URL = "https://api.upbit.com/v1"


class UpbitAdapter(BrokerAdapter):
    name = "upbit"

    def __init__(self, access_key: str, secret_key: str) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _make_token(self, query: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "access_key": self._access_key,
            "nonce": str(uuid.uuid4()),
        }
        if query:
            query_string = urlencode(query)
            m = hashlib.sha512()
            m.update(query_string.encode())
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        return jwt.encode(payload, self._secret_key, algorithm="HS256")

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        session = await self._get_session()
        url = f"{UPBIT_API_URL}{path}"
        headers = {}

        query_for_token = params or body
        if self._access_key:
            token = self._make_token(query_for_token)
            headers["Authorization"] = f"Bearer {token}"

        async with session.request(
            method, url, params=params, json=body, headers=headers
        ) as resp:
            data = await resp.json()
            if resp.status != 200 and resp.status != 201:
                error_msg = data.get("error", {}).get("message", str(data))
                logger.error("Upbit API error: %s %s -> %s", method, path, error_msg)
                raise RuntimeError(f"Upbit API error: {error_msg}")
            return data

    async def buy(self, symbol: str, amount: float) -> TradeResult:
        try:
            body = {
                "market": symbol,
                "side": "bid",
                "ord_type": "price",
                "price": str(amount),
            }
            result = await self._request("POST", "/orders", body=body)
            return TradeResult(
                success=True,
                order_id=result.get("uuid", ""),
                symbol=symbol,
                side="buy",
                amount=amount,
                price=float(result.get("price", 0)),
                volume=float(result.get("executed_volume", 0)),
                fee=float(result.get("paid_fee", 0)),
            )
        except Exception as e:
            logger.error("Buy failed: %s", e)
            return TradeResult(success=False, symbol=symbol, side="buy", error=str(e))

    async def sell(self, symbol: str, volume: float) -> TradeResult:
        try:
            body = {
                "market": symbol,
                "side": "ask",
                "ord_type": "market",
                "volume": str(volume),
            }
            result = await self._request("POST", "/orders", body=body)
            return TradeResult(
                success=True,
                order_id=result.get("uuid", ""),
                symbol=symbol,
                side="sell",
                volume=volume,
                price=float(result.get("price", 0)),
                amount=float(result.get("executed_volume", 0))
                * float(result.get("price", 0)),
                fee=float(result.get("paid_fee", 0)),
            )
        except Exception as e:
            logger.error("Sell failed: %s", e)
            return TradeResult(success=False, symbol=symbol, side="sell", error=str(e))

    async def get_portfolio(self) -> Portfolio:
        accounts = await self._request("GET", "/accounts")
        items: list[PortfolioItem] = []
        krw_balance = 0.0
        krw_available = 0.0

        for acc in accounts:
            currency = acc["currency"]
            balance = float(acc["balance"])
            locked = float(acc["locked"])
            avg_price = float(acc["avg_buy_price"])

            if currency == "KRW":
                krw_balance = balance + locked
                krw_available = balance
                continue

            if balance > 0:
                symbol = f"KRW-{currency}"
                try:
                    current_price = await self.get_current_price(symbol)
                except Exception:
                    current_price = avg_price

                items.append(
                    PortfolioItem(
                        symbol=symbol,
                        balance=balance,
                        avg_buy_price=avg_price,
                        current_price=current_price,
                    )
                )

        return Portfolio(
            broker="upbit",
            total_balance=krw_balance,
            available_balance=krw_available,
            items=items,
        )

    async def get_market_data(
        self, symbol: str, interval: str = "5m", count: int = 200
    ) -> list[MarketData]:
        interval_map = {
            "1m": ("minutes/1", count),
            "3m": ("minutes/3", count),
            "5m": ("minutes/5", count),
            "15m": ("minutes/15", count),
            "30m": ("minutes/30", count),
            "1h": ("minutes/60", count),
            "4h": ("minutes/240", count),
            "1d": ("days", count),
            "1w": ("weeks", count),
        }
        path_suffix, cnt = interval_map.get(interval, ("minutes/5", count))
        params = {"market": symbol, "count": min(cnt, 200)}
        data = await self._request("GET", f"/candles/{path_suffix}", params=params)

        return [
            MarketData(
                symbol=symbol,
                timestamp=candle["candle_date_time_kst"],
                open=float(candle["opening_price"]),
                high=float(candle["high_price"]),
                low=float(candle["low_price"]),
                close=float(candle["trade_price"]),
                volume=float(candle["candle_acc_trade_volume"]),
            )
            for candle in reversed(data)  # 오래된 순서로 정렬
        ]

    async def get_orderbook(self, symbol: str) -> Orderbook:
        data = await self._request("GET", "/orderbook", params={"markets": symbol})
        book = data[0] if data else {}
        return Orderbook(
            symbol=symbol,
            asks=[
                {"price": u["ask_price"], "volume": u["ask_size"]}
                for u in book.get("orderbook_units", [])
            ],
            bids=[
                {"price": u["bid_price"], "volume": u["bid_size"]}
                for u in book.get("orderbook_units", [])
            ],
            timestamp=str(book.get("timestamp", "")),
        )

    async def get_historical_data(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 30,
    ) -> list[MarketData]:
        """히스토리컬 데이터 pagination (200캔들 제한 우회).

        Upbit API는 최대 200캔들/요청. 30일 5분봉 = 8640캔들 → 44회 요청.
        to 파라미터로 과거 방향 pagination.
        """
        interval_map = {
            "1m": "minutes/1",
            "3m": "minutes/3",
            "5m": "minutes/5",
            "15m": "minutes/15",
            "1h": "minutes/60",
            "4h": "minutes/240",
            "1d": "days",
        }
        path_suffix = interval_map.get(interval, "minutes/5")

        # 간격별 분 수
        interval_minutes = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        mins = interval_minutes.get(interval, 5)
        total_candles = (days * 24 * 60) // mins

        all_data: list[MarketData] = []
        to_param: str | None = None
        remaining = total_candles

        while remaining > 0:
            batch_size = min(remaining, 200)
            params: dict[str, Any] = {
                "market": symbol,
                "count": batch_size,
            }
            if to_param:
                params["to"] = to_param

            data = await self._request(
                "GET", f"/candles/{path_suffix}", params=params
            )
            if not data:
                break

            batch = [
                MarketData(
                    symbol=symbol,
                    timestamp=c["candle_date_time_kst"],
                    open=float(c["opening_price"]),
                    high=float(c["high_price"]),
                    low=float(c["low_price"]),
                    close=float(c["trade_price"]),
                    volume=float(c["candle_acc_trade_volume"]),
                )
                for c in data
            ]
            all_data.extend(batch)

            # 다음 페이지: 마지막 캔들의 시간을 to로
            to_param = data[-1]["candle_date_time_utc"]
            remaining -= len(batch)

            # Rate limit: 초당 10회 제한
            await asyncio.sleep(0.11)

        # 시간순 정렬 (오래된 → 최신)
        all_data.reverse()
        logger.info(
            "Fetched %d candles for %s (%s, %d days)",
            len(all_data), symbol, interval, days,
        )
        return all_data

    async def get_current_price(self, symbol: str) -> float:
        data = await self._request("GET", "/ticker", params={"markets": symbol})
        return float(data[0]["trade_price"])

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
