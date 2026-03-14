from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from src.brokers.base import MarketData


@dataclass
class TradeSignal:
    side: Literal["buy", "sell"]
    symbol: str
    amount: float
    confidence: float  # 0.0 ~ 1.0
    reason: str


@dataclass
class BacktestResult:
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StrategyContext:
    market_data: dict[str, list[MarketData]]  # symbol -> OHLCV list
    portfolio_value: float
    current_positions: dict[str, float]  # symbol -> volume
    params: dict[str, Any]


class Strategy(ABC):
    id: str
    name: str
    broker: str  # "upbit" | "kiwoom"
    symbols: list[str]
    capital_allocation: float
    interval_minutes: int = 5
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        *,
        id: str,
        name: str,
        broker: str,
        symbols: list[str],
        capital_allocation: float,
        interval_minutes: int = 5,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.broker = broker
        self.symbols = symbols
        self.capital_allocation = capital_allocation
        self.interval_minutes = interval_minutes
        self.params = params or self.default_params()

    @abstractmethod
    def default_params(self) -> dict[str, Any]:
        """전략 기본 파라미터"""

    @abstractmethod
    async def execute(self, ctx: StrategyContext) -> list[TradeSignal]:
        """시장 데이터 분석 -> 매매 시그널 반환"""

    @abstractmethod
    async def backtest(self, historical: dict[str, list[MarketData]]) -> BacktestResult:
        """히스토리컬 데이터로 백테스트"""

    def update_params(self, new_params: dict[str, Any]) -> None:
        self.params.update(new_params)
