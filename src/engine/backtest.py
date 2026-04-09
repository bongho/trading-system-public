"""백테스트 엔진 - 캔들 순회 시뮬레이션."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from src.brokers.base import MarketData
from src.strategies.base import BacktestResult

logger = logging.getLogger(__name__)

UPBIT_FEE_PCT = 0.0005  # 0.05%
SLIPPAGE_PCT = 0.001  # 0.1%


@dataclass
class Position:
    symbol: str
    entry_price: float
    volume: float
    entry_idx: int

    @property
    def cost(self) -> float:
        return self.entry_price * self.volume


@dataclass
class BacktestTrade:
    idx: int
    symbol: str
    side: str
    price: float
    volume: float
    amount: float
    fee: float
    pnl: float = 0.0
    pnl_pct: float = 0.0


class BacktestEngine:
    """캔들 순회 기반 백테스트 엔진.

    전략의 execute()를 직접 호출하지 않고, 전략이 자체 백테스트 로직을
    구현할 수 있도록 유틸리티를 제공합니다.
    """

    def __init__(
        self,
        initial_capital: float = 100000,
        fee_pct: float = UPBIT_FEE_PCT,
        slippage_pct: float = SLIPPAGE_PCT,
    ) -> None:
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

    def run_simple(
        self,
        candles: list[MarketData],
        signals: list[dict[str, Any]],
    ) -> BacktestResult:
        """시그널 리스트 기반 간단 백테스트.

        signals: [{"idx": candle_index, "side": "buy"|"sell", "symbol": str}]
        매수: 다음 캔들 시가에 체결, 자본의 일정 비율
        매도: 다음 캔들 시가에 전량 체결
        """
        capital = self.initial_capital
        positions: dict[str, Position] = {}
        trades: list[BacktestTrade] = []
        equity_curve: list[float] = [capital]
        peak = capital

        for sig in signals:
            idx = sig["idx"]
            if idx + 1 >= len(candles):
                continue

            fill_price = candles[idx + 1].open
            symbol = sig["symbol"]

            if sig["side"] == "buy" and symbol not in positions:
                # 슬리피지 적용 (매수는 약간 높게)
                price = fill_price * (1 + self.slippage_pct)
                # 자본의 20%로 매수
                trade_amount = capital * 0.2
                fee = trade_amount * self.fee_pct
                volume = (trade_amount - fee) / price

                positions[symbol] = Position(
                    symbol=symbol,
                    entry_price=price,
                    volume=volume,
                    entry_idx=idx,
                )
                capital -= trade_amount
                trades.append(
                    BacktestTrade(
                        idx=idx,
                        symbol=symbol,
                        side="buy",
                        price=price,
                        volume=volume,
                        amount=trade_amount,
                        fee=fee,
                    )
                )

            elif sig["side"] == "sell" and symbol in positions:
                pos = positions.pop(symbol)
                # 슬리피지 적용 (매도는 약간 낮게)
                price = fill_price * (1 - self.slippage_pct)
                amount = price * pos.volume
                fee = amount * self.fee_pct
                net = amount - fee

                pnl = net - pos.cost
                pnl_pct = pnl / pos.cost if pos.cost > 0 else 0

                capital += net
                trades.append(
                    BacktestTrade(
                        idx=idx,
                        symbol=symbol,
                        side="sell",
                        price=price,
                        volume=pos.volume,
                        amount=amount,
                        fee=fee,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                    )
                )

            # 이퀴티 커브 업데이트
            unrealized = sum(
                candles[min(idx, len(candles) - 1)].close * p.volume
                for p in positions.values()
            )
            equity = capital + unrealized
            equity_curve.append(equity)
            peak = max(peak, equity)

        # 미청산 포지션 강제 청산 (마지막 캔들 종가)
        if positions and candles:
            last_price = candles[-1].close
            for symbol, pos in list(positions.items()):
                price = last_price * (1 - self.slippage_pct)
                amount = price * pos.volume
                fee = amount * self.fee_pct
                net = amount - fee
                pnl = net - pos.cost
                pnl_pct = pnl / pos.cost if pos.cost > 0 else 0
                capital += net
                trades.append(
                    BacktestTrade(
                        idx=len(candles) - 1,
                        symbol=symbol,
                        side="sell",
                        price=price,
                        volume=pos.volume,
                        amount=amount,
                        fee=fee,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                    )
                )

        return self._calc_result(trades, equity_curve)

    def _calc_result(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[float],
    ) -> BacktestResult:
        sell_trades = [t for t in trades if t.side == "sell"]
        wins = [t for t in sell_trades if t.pnl > 0]
        losses = [t for t in sell_trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in sell_trades)
        total_pnl_pct = (total_pnl / self.initial_capital) * 100 if self.initial_capital else 0

        # Max Drawdown
        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Sharpe Ratio (일간 수익률 기준, 간략)
        sharpe = 0.0
        if len(equity_curve) > 2:
            returns = [
                (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
                for i in range(1, len(equity_curve))
                if equity_curve[i - 1] > 0
            ]
            if returns:
                avg_ret = sum(returns) / len(returns)
                std_ret = (
                    sum((r - avg_ret) ** 2 for r in returns) / len(returns)
                ) ** 0.5
                if std_ret > 0:
                    sharpe = (avg_ret / std_ret) * math.sqrt(252)

        return BacktestResult(
            total_trades=len(sell_trades),
            win_count=len(wins),
            loss_count=len(losses),
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            max_drawdown=max_dd * 100,
            sharpe_ratio=sharpe,
            win_rate=(len(wins) / len(sell_trades) * 100) if sell_trades else 0,
            avg_profit=sum(t.pnl for t in wins) / len(wins) if wins else 0,
            avg_loss=sum(t.pnl for t in losses) / len(losses) if losses else 0,
            trades=[
                {
                    "side": t.side,
                    "symbol": t.symbol,
                    "price": t.price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                }
                for t in sell_trades[-10:]  # 최근 10건만
            ],
        )
