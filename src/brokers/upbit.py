from __future__ import annotations

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

    async def get_current_price(self, symbol: str) -> float:
        data = await self._request("GET", "/ticker", params={"markets": symbol})
        return float(data[0]["trade_price"])

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
