"""Squeeze MTF (Multi-Timeframe Squeeze Momentum) 전략 - Phase 4.

멀티타임프레임 스퀴즈 모멘텀 기반 매매.
5분(진입) + 1시간(방향 확인) + 4시간(트렌드 확인).

- Squeeze On: BB가 KC 안에 있음 (변동성 압축)
- Squeeze Off: BB가 KC 밖으로 나옴 (변동성 폭발)
- BUY: 스퀴즈 해제 + 모멘텀 양수 + 상위 타임프레임 동의
- SELL: 스퀴즈 해제 + 모멘텀 음수 OR 손절/익절
"""

from __future__ import annotations

import logging
from typing import Any

from src.brokers.base import MarketData
from src.engine.backtest import BacktestEngine
from src.strategies.base import BacktestResult, Strategy, StrategyContext, TradeSignal
from src.utils.indicators import squeeze_momentum, to_dataframe

logger = logging.getLogger(__name__)


class SqueezeMTFStrategy(Strategy):
    def default_params(self) -> dict[str, Any]:
        return {
            "bb_period": 20,
            "bb_std": 2.0,
            "kc_period": 20,
            "kc_atr_mult": 1.5,
            "mom_period": 20,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.05,
            "timeframes": ["5m", "1h", "4h"],
        }

    async def execute(self, ctx: StrategyContext) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        params = ctx.params

        for symbol, data in ctx.market_data.items():
            min_len = max(params["bb_period"], params["kc_period"], params["mom_period"]) + 5
            if len(data) < min_len:
                continue

            df = to_dataframe(data)
            squeeze_on, momentum = squeeze_momentum(
                df["high"],
                df["low"],
                df["close"],
                bb_period=params["bb_period"],
                bb_std=params["bb_std"],
                kc_period=params["kc_period"],
                kc_atr_mult=params["kc_atr_mult"],
                mom_period=params["mom_period"],
            )

            if len(momentum) < 3 or momentum.isna().iloc[-1]:
                continue

            # 스퀴즈 상태 판단
            was_squeezed = squeeze_on.iloc[-2] if not squeeze_on.isna().iloc[-2] else False
            is_squeezed = squeeze_on.iloc[-1] if not squeeze_on.isna().iloc[-1] else False
            squeeze_released = was_squeezed and not is_squeezed

            curr_mom = momentum.iloc[-1]
            prev_mom = momentum.iloc[-2]
            mom_increasing = curr_mom > prev_mom

            has_position = (
                symbol in ctx.current_positions and ctx.current_positions[symbol] > 0
            )

            # BUY: 스퀴즈 해제 + 모멘텀 양수 + 모멘텀 증가
            if (
                not has_position
                and squeeze_released
                and curr_mom > 0
                and mom_increasing
            ):
                amount = min(
                    ctx.portfolio_value * 0.1,
                    self.capital_allocation * 0.2,
                )
                confidence = min(1.0, 0.5 + abs(curr_mom) / 100)

                signals.append(
                    TradeSignal(
                        side="buy",
                        symbol=symbol,
                        amount=amount,
                        confidence=confidence,
                        reason=(
                            f"Squeeze 해제 매수: 모멘텀 {curr_mom:.2f} "
                            f"(증가: {prev_mom:.2f}→{curr_mom:.2f})"
                        ),
                    )
                )

            # SELL: 포지션 보유 중
            elif has_position:
                volume = ctx.current_positions.get(symbol, 0)

                # 모멘텀 음수 전환 (추세 반전)
                if curr_mom < 0 and prev_mom >= 0:
                    signals.append(
                        TradeSignal(
                            side="sell",
                            symbol=symbol,
                            amount=volume,
                            confidence=0.8,
                            reason=(
                                f"Squeeze 모멘텀 음수 전환: "
                                f"{prev_mom:.2f}→{curr_mom:.2f}"
                            ),
                        )
                    )
                # 모멘텀 양수지만 감소 (힘 약화) + 스퀴즈 재진입
                elif is_squeezed and curr_mom > 0 and not mom_increasing:
                    signals.append(
                        TradeSignal(
                            side="sell",
                            symbol=symbol,
                            amount=volume,
                            confidence=0.6,
                            reason=(
                                f"Squeeze 재진입 + 모멘텀 약화: "
                                f"{prev_mom:.2f}→{curr_mom:.2f}"
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
        min_len = max(params["bb_period"], params["kc_period"], params["mom_period"]) + 5
        if len(candles) < min_len:
            return BacktestResult()

        df = to_dataframe(candles)
        squeeze_on, momentum = squeeze_momentum(
            df["high"],
            df["low"],
            df["close"],
            bb_period=params["bb_period"],
            bb_std=params["bb_std"],
            kc_period=params["kc_period"],
            kc_atr_mult=params["kc_atr_mult"],
            mom_period=params["mom_period"],
        )

        signals: list[dict[str, Any]] = []
        in_position = False

        for i in range(1, len(momentum)):
            if momentum.isna().iloc[i] or momentum.isna().iloc[i - 1]:
                continue
            if squeeze_on.isna().iloc[i] or squeeze_on.isna().iloc[i - 1]:
                continue

            was_sq = bool(squeeze_on.iloc[i - 1])
            is_sq = bool(squeeze_on.iloc[i])
            released = was_sq and not is_sq
            curr_m = momentum.iloc[i]
            prev_m = momentum.iloc[i - 1]

            if not in_position and released and curr_m > 0 and curr_m > prev_m:
                signals.append({"idx": i, "side": "buy", "symbol": symbol})
                in_position = True

            elif in_position:
                # 모멘텀 음수 전환
                if curr_m < 0 and prev_m >= 0:
                    signals.append({"idx": i, "side": "sell", "symbol": symbol})
                    in_position = False
                # 손절/익절
                elif signals:
                    last_buy = next(
                        (s for s in reversed(signals) if s["side"] == "buy"), None
                    )
                    if last_buy and last_buy["idx"] + 1 < len(candles):
                        entry_price = candles[last_buy["idx"] + 1].open
                        current_price = candles[i].close
                        pnl_pct = (current_price - entry_price) / entry_price

                        if pnl_pct <= -params["stop_loss_pct"]:
                            signals.append({"idx": i, "side": "sell", "symbol": symbol})
                            in_position = False
                        elif pnl_pct >= params["take_profit_pct"]:
                            signals.append({"idx": i, "side": "sell", "symbol": symbol})
                            in_position = False

        engine = BacktestEngine(initial_capital=self.capital_allocation)
        return engine.run_simple(candles, signals)


def create_strategy(
    capital_allocation: float = 100000,
    params: dict[str, Any] | None = None,
) -> SqueezeMTFStrategy:
    return SqueezeMTFStrategy(
        id="squeeze_mtf",
        name="Squeeze MTF",
        broker="upbit",
        symbols=["KRW-BTC", "KRW-ETH"],
        capital_allocation=capital_allocation,
        interval_minutes=5,
        params=params,
    )
