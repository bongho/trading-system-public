"""Double BB Short (이중 볼린저밴드) 전략 - Phase 4.

내측 BB(1σ)와 외측 BB(2σ) 사이의 가격 움직임 기반 매매.
- BUY: 외측 하단 BB 이탈 + RSI 과매도 → 반등 진입
- SELL: 내측 상단 BB 도달 (부분 익절) / 외측 상단 BB 도달 (전량 청산)
- STOP: 외측 BB 아래 stop_loss_mult 배수 이탈 시 손절
"""

from __future__ import annotations

from typing import Any

from src.brokers.base import MarketData
from src.engine.backtest import BacktestEngine
from src.strategies.base import BacktestResult, Strategy, StrategyContext, TradeSignal
from src.utils.indicators import bollinger_bands, rsi, to_dataframe


class DoubleBBShortStrategy(Strategy):
    def default_params(self) -> dict[str, Any]:
        return {
            "bb_period": 20,
            "inner_std": 1.0,
            "outer_std": 2.0,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "partial_tp_pct": 0.5,
            "stop_loss_mult": 1.0,
        }

    async def execute(self, ctx: StrategyContext) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = ctx.params

        for symbol, data in ctx.market_data.items():
            min_len = params["bb_period"] + params["rsi_period"] + 2
            if len(data) < min_len:
                continue

            df = to_dataframe(data)
            close = df["close"]

            # 이중 볼린저밴드
            outer_upper, outer_mid, outer_lower = bollinger_bands(
                close, params["bb_period"], params["outer_std"]
            )
            inner_upper, inner_mid, inner_lower = bollinger_bands(
                close, params["bb_period"], params["inner_std"]
            )
            rsi_series = rsi(close, params["rsi_period"])

            if len(rsi_series) < 2:
                continue

            current_price = close.iloc[-1]
            current_rsi = rsi_series.iloc[-1]
            prev_rsi = rsi_series.iloc[-2]
            has_position = (
                symbol in ctx.current_positions and ctx.current_positions[symbol] > 0
            )

            # BUY: 외측 하단 BB 이하 + RSI 과매도 + RSI 반등
            if (
                not has_position
                and current_price <= outer_lower.iloc[-1]
                and current_rsi < params["rsi_oversold"]
                and current_rsi > prev_rsi
            ):
                amount = min(
                    ctx.portfolio_value * 0.1,
                    self.capital_allocation * 0.2,
                )
                # 외측 BB와 가격 거리 기반 신뢰도
                bb_dist = (outer_lower.iloc[-1] - current_price) / outer_lower.iloc[-1]
                confidence = min(1.0, 0.5 + bb_dist * 10)

                signals.append(
                    TradeSignal(
                        side="buy",
                        symbol=symbol,
                        amount=amount,
                        confidence=confidence,
                        reason=(
                            f"DoubleBB 매수: 가격({current_price:.0f}) <= "
                            f"외측하단BB({outer_lower.iloc[-1]:.0f}), "
                            f"RSI {current_rsi:.1f} 반등"
                        ),
                    )
                )

            # SELL: 내측 상단 BB 이상 (부분 익절) 또는 외측 상단 BB 이상 (전량)
            elif has_position:
                volume = ctx.current_positions.get(symbol, 0)

                if current_price >= outer_upper.iloc[-1] and current_rsi > params["rsi_overbought"]:
                    # 전량 청산: 외측 상단 + RSI 과매수
                    signals.append(
                        TradeSignal(
                            side="sell",
                            symbol=symbol,
                            amount=volume,
                            confidence=0.9,
                            reason=(
                                f"DoubleBB 전량청산: 가격({current_price:.0f}) >= "
                                f"외측상단BB({outer_upper.iloc[-1]:.0f}), "
                                f"RSI {current_rsi:.1f}"
                            ),
                        )
                    )
                elif current_price >= inner_upper.iloc[-1] and current_rsi > params["rsi_overbought"]:
                    # 부분 익절
                    partial = volume * params["partial_tp_pct"]
                    signals.append(
                        TradeSignal(
                            side="sell",
                            symbol=symbol,
                            amount=partial,
                            confidence=0.7,
                            reason=(
                                f"DoubleBB 부분익절({params['partial_tp_pct']:.0%}): "
                                f"가격({current_price:.0f}) >= "
                                f"내측상단BB({inner_upper.iloc[-1]:.0f})"
                            ),
                        )
                    )

        return signals

    async def backtest(self, historical: dict[str, list[MarketData]]) -> BacktestResult:
        symbol = next(iter(historical), None)
        if not symbol:
            return BacktestResult()

        candles = historical[symbol]
        params = self.params
        min_len = params["bb_period"] + params["rsi_period"] + 2
        if len(candles) < min_len:
            return BacktestResult()

        df = to_dataframe(candles)
        close = df["close"]

        outer_upper, _, outer_lower = bollinger_bands(
            close, params["bb_period"], params["outer_std"]
        )
        inner_upper, _, _ = bollinger_bands(
            close, params["bb_period"], params["inner_std"]
        )
        rsi_series = rsi(close, params["rsi_period"])

        signals: list[dict[str, Any]] = []
        in_position = False

        for i in range(1, len(close)):
            if (
                rsi_series.isna().iloc[i]
                or rsi_series.isna().iloc[i - 1]
                or outer_upper.isna().iloc[i]
            ):
                continue

            price = close.iloc[i]
            curr_rsi = rsi_series.iloc[i]
            prev_rsi = rsi_series.iloc[i - 1]

            if (
                not in_position
                and price <= outer_lower.iloc[i]
                and curr_rsi < params["rsi_oversold"]
                and curr_rsi > prev_rsi
            ):
                signals.append({"idx": i, "side": "buy", "symbol": symbol})
                in_position = True

            elif in_position:
                # 전량 청산: 외측 상단
                if price >= outer_upper.iloc[i] and curr_rsi > params["rsi_overbought"]:
                    signals.append({"idx": i, "side": "sell", "symbol": symbol})
                    in_position = False
                # 손절: 진입가 대비
                elif signals:
                    last_buy = next(
                        (s for s in reversed(signals) if s["side"] == "buy"), None
                    )
                    if last_buy and last_buy["idx"] + 1 < len(candles):
                        entry_price = candles[last_buy["idx"] + 1].open
                        bb_width = outer_upper.iloc[i] - outer_lower.iloc[i]
                        stop = entry_price - bb_width * params["stop_loss_mult"]
                        if price <= stop:
                            signals.append({"idx": i, "side": "sell", "symbol": symbol})
                            in_position = False

        engine = BacktestEngine(initial_capital=self.capital_allocation)
        return engine.run_simple(candles, signals)


def create_strategy(
    capital_allocation: float = 100000,
    params: dict[str, Any] | None = None,
) -> DoubleBBShortStrategy:
    return DoubleBBShortStrategy(
        id="double_bb_short",
        name="Double BB Short",
        broker="upbit",
        symbols=["KRW-BTC", "KRW-ETH"],
        capital_allocation=capital_allocation,
        interval_minutes=5,
        params=params,
    )
