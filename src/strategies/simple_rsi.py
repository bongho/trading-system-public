"""SimpleRSI 전략 - Phase 2 시스템 검증용.

RSI 과매도 구간에서 매수, 과매수 구간에서 매도.
"""

from __future__ import annotations

from typing import Any

from src.brokers.base import MarketData
from src.strategies.base import BacktestResult, Strategy, StrategyContext, TradeSignal
from src.utils.indicators import rsi, to_dataframe


class SimpleRSIStrategy(Strategy):
    def default_params(self) -> dict[str, Any]:
        return {
            "rsi_period": 14,
            "buy_threshold": 30,
            "sell_threshold": 70,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.03,
            "trailing_stop_pct": 0.015,
        }

    async def execute(self, ctx: StrategyContext) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = ctx.params

        for symbol, data in ctx.market_data.items():
            if len(data) < params["rsi_period"] + 2:
                continue

            df = to_dataframe(data)
            rsi_series = rsi(df["close"], params["rsi_period"])

            if len(rsi_series) < 2:
                continue

            current_rsi = rsi_series.iloc[-1]
            prev_rsi = rsi_series.iloc[-2]
            has_position = (
                symbol in ctx.current_positions and ctx.current_positions[symbol] > 0
            )

            # BUY: RSI < 30 AND RSI 상승 전환
            if (
                not has_position
                and current_rsi < params["buy_threshold"]
                and current_rsi > prev_rsi
            ):
                # 전략 자본의 일정 비율로 매수
                amount = min(
                    ctx.portfolio_value * 0.1,
                    self.capital_allocation * 0.2,
                )
                signals.append(
                    TradeSignal(
                        side="buy",
                        symbol=symbol,
                        amount=amount,
                        confidence=min(
                            1.0, (params["buy_threshold"] - current_rsi) / 10
                        ),
                        reason=f"RSI 과매도 반등 (RSI: {current_rsi:.1f} → {prev_rsi:.1f})",
                    )
                )

            # SELL: RSI > 70 AND RSI 하락 전환
            elif (
                has_position
                and current_rsi > params["sell_threshold"]
                and current_rsi < prev_rsi
            ):
                volume = ctx.current_positions.get(symbol, 0)
                signals.append(
                    TradeSignal(
                        side="sell",
                        symbol=symbol,
                        amount=volume,
                        confidence=min(
                            1.0, (current_rsi - params["sell_threshold"]) / 10
                        ),
                        reason=f"RSI 과매수 하락 (RSI: {current_rsi:.1f} → {prev_rsi:.1f})",
                    )
                )

        return signals

    async def backtest(self, historical: dict[str, list[MarketData]]) -> BacktestResult:
        # Phase 2에서 상세 구현
        return BacktestResult()


def create_strategy(
    capital_allocation: float = 100000,
    params: dict[str, Any] | None = None,
) -> SimpleRSIStrategy:
    return SimpleRSIStrategy(
        id="simple_rsi",
        name="SimpleRSI",
        broker="upbit",
        symbols=["KRW-BTC", "KRW-ETH"],
        capital_allocation=capital_allocation,
        interval_minutes=5,
        params=params,
    )
