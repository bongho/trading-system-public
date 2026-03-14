from __future__ import annotations

import logging
from dataclasses import dataclass

from src.strategies.base import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""


class RiskManager:
    def __init__(
        self,
        max_daily_loss_pct: float = 0.10,
        max_position_pct: float = 0.30,
        max_single_trade_pct: float = 0.20,
    ) -> None:
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_position_pct = max_position_pct
        self._max_single_trade_pct = max_single_trade_pct
        self._daily_pnl: dict[str, float] = {}  # strategy_id -> daily pnl

    def validate(
        self,
        signal: TradeSignal,
        strategy_id: str,
        capital: float,
        current_positions: dict[str, float],
    ) -> RiskCheckResult:
        # 1. 일일 최대 손실 체크
        daily_loss = self._daily_pnl.get(strategy_id, 0)
        if daily_loss < 0 and abs(daily_loss) / capital > self._max_daily_loss_pct:
            return RiskCheckResult(
                approved=False,
                reason=f"일일 최대 손실 초과: {abs(daily_loss) / capital:.1%} > {self._max_daily_loss_pct:.1%}",
            )

        # 2. 단일 매매 금액 체크
        if signal.side == "buy":
            if signal.amount / capital > self._max_single_trade_pct:
                return RiskCheckResult(
                    approved=False,
                    reason=f"단일 매매 금액 초과: {signal.amount / capital:.1%} > {self._max_single_trade_pct:.1%}",
                )

        # 3. 포지션 집중도 체크
        if signal.side == "buy" and signal.symbol in current_positions:
            existing = current_positions.get(signal.symbol, 0)
            if existing > 0:
                return RiskCheckResult(
                    approved=False,
                    reason=f"이미 {signal.symbol} 포지션 보유 중",
                )

        # 4. 최소 confidence 체크
        if signal.confidence < 0.3:
            return RiskCheckResult(
                approved=False,
                reason=f"낮은 신뢰도: {signal.confidence:.2f} < 0.30",
            )

        return RiskCheckResult(approved=True)

    def record_pnl(self, strategy_id: str, pnl: float) -> None:
        self._daily_pnl[strategy_id] = self._daily_pnl.get(strategy_id, 0) + pnl

    def reset_daily(self) -> None:
        self._daily_pnl.clear()
