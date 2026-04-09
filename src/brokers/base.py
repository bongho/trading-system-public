from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TradeResult:
    success: bool
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    fee: float = 0.0
    error: str = ""


@dataclass
class PortfolioItem:
    symbol: str
    balance: float
    avg_buy_price: float
    current_price: float = 0.0

    @property
    def value(self) -> float:
        return self.balance * self.current_price

    @property
    def pnl_pct(self) -> float:
        if self.avg_buy_price <= 0:
            return 0.0
        return (self.current_price - self.avg_buy_price) / self.avg_buy_price


@dataclass
class Portfolio:
    broker: str
    total_balance: float
    available_balance: float
    items: list[PortfolioItem] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return self.available_balance + sum(item.value for item in self.items)


@dataclass
class MarketData:
    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Orderbook:
    symbol: str
    asks: list[dict[str, float]]  # [{price, volume}]
    bids: list[dict[str, float]]
    timestamp: str = ""


class BrokerAdapter(ABC):
    name: str = ""

    @abstractmethod
    async def buy(self, symbol: str, amount: float) -> TradeResult:
        """시장가 매수 (amount: KRW 금액)"""

    @abstractmethod
    async def sell(self, symbol: str, volume: float) -> TradeResult:
        """시장가 매도 (volume: 코인/주식 수량)"""

    @abstractmethod
    async def get_portfolio(self) -> Portfolio:
        """전체 포트폴리오 조회"""

    @abstractmethod
    async def get_market_data(
        self, symbol: str, interval: str = "5m", count: int = 200
    ) -> list[MarketData]:
        """OHLCV 시장 데이터 조회"""

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> Orderbook:
        """호가 조회"""

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        """현재가 조회"""

    async def close(self) -> None:
        """리소스 정리"""


def get_broker(broker_name: str, **kwargs: Any) -> BrokerAdapter:
    if broker_name == "upbit":
        from src.brokers.upbit import UpbitAdapter

        return UpbitAdapter(**kwargs)
    if broker_name == "kiwoom":
        from src.brokers.kiwoom import KiwoomAdapter

        return KiwoomAdapter(**kwargs)
    raise ValueError(f"Unknown broker: {broker_name}")
