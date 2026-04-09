from __future__ import annotations

import logging
from typing import Any

from src.brokers.base import BrokerAdapter, MarketData
from src.db.repository import MarketDataRepository, TradeRepository
from src.engine.risk_manager import RiskManager
from src.strategies.base import Strategy, StrategyContext, TradeSignal

# SwarmConsensus는 선택적 의존성 — 순환 임포트 방지를 위해 TYPE_CHECKING 사용
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agents.swarm import SwarmConsensus
    from src.agents.models import AgentContext

logger = logging.getLogger(__name__)


class Executor:
    def __init__(
        self,
        brokers: dict[str, BrokerAdapter],
        risk_manager: RiskManager,
        trade_repo: TradeRepository,
        notify_callback: Any | None = None,
        market_data_repo: MarketDataRepository | None = None,
        swarm: SwarmConsensus | None = None,
    ) -> None:
        self._brokers = brokers
        self._risk = risk_manager
        self._trade_repo = trade_repo
        self._notify = notify_callback
        self._market_repo = market_data_repo
        self._swarm = swarm
        self.last_signals: dict[
            str, list[dict[str, Any]]
        ] = {}  # strategy_id -> signals

    async def run_strategy(self, strategy: Strategy) -> list[dict[str, Any]]:
        """단일 전략 실행: 시장 데이터 수집 → 시그널 → 리스크 체크 → 주문"""
        if not strategy.enabled:
            return []

        broker = self._brokers.get(strategy.broker)
        if not broker:
            logger.error("Broker not found: %s", strategy.broker)
            return []

        results = []
        try:
            # 1. 시장 데이터 수집 + write-through 캐시
            market_data: dict[str, list[MarketData]] = {}
            for symbol in strategy.symbols:
                data = await broker.get_market_data(symbol, "5m", 200)
                market_data[symbol] = data
                # DB에 자동 저장 (비동기, 실패해도 전략 실행에 영향 없음)
                if self._market_repo and data:
                    try:
                        await self._market_repo.upsert_candles(data, "5m")
                    except Exception as e:
                        logger.warning("Failed to cache market data: %s", e)

            # 2. 포트폴리오 조회
            portfolio = await broker.get_portfolio()
            current_positions = {item.symbol: item.balance for item in portfolio.items}

            # 3. 전략 실행
            ctx = StrategyContext(
                market_data=market_data,
                portfolio_value=portfolio.total_value,
                current_positions=current_positions,
                params=strategy.params,
            )
            signals = await strategy.execute(ctx)

            # 시그널 기록 (디버깅용)
            self.last_signals[strategy.id] = [
                {
                    "symbol": s.symbol,
                    "side": s.side,
                    "amount": s.amount,
                    "confidence": s.confidence,
                    "reason": s.reason,
                }
                for s in signals
            ]

            # 4. 각 시그널 처리
            for signal in signals:
                result = await self._process_signal(
                    signal, strategy, broker, current_positions, market_data
                )
                if result:
                    results.append(result)

        except Exception as e:
            logger.error(
                "Strategy %s execution failed: %s", strategy.id, e, exc_info=True
            )

        return results

    async def _process_signal(
        self,
        signal: TradeSignal,
        strategy: Strategy,
        broker: BrokerAdapter,
        current_positions: dict[str, float],
        market_data: dict[str, list[MarketData]] | None = None,
    ) -> dict[str, Any] | None:
        # 리스크 체크
        risk_result = self._risk.validate(
            signal, strategy.id, strategy.capital_allocation, current_positions
        )
        if not risk_result.approved:
            logger.info(
                "Signal rejected by risk manager: %s - %s",
                signal.symbol,
                risk_result.reason,
            )
            return None

        # Swarm Consensus 체크 (설정된 경우)
        if self._swarm is not None:
            try:
                from src.agents.models import AgentContext
                ctx = AgentContext(market_data={
                    symbol: {
                        "candles": len(candles),
                        "last_close": candles[-1].close if candles else 0,
                        "last_high": candles[-1].high if candles else 0,
                        "last_low": candles[-1].low if candles else 0,
                        "last_volume": candles[-1].volume if candles else 0,
                    }
                    for symbol, candles in (market_data or {}).items()
                    if candles
                })
                consensus = await self._swarm.evaluate(signal, ctx)
                if not consensus.approved:
                    logger.info(
                        "Signal rejected by swarm consensus [%s]: %s",
                        consensus.quorum_str,
                        consensus.summary,
                    )
                    return None
                logger.info(
                    "Signal approved by swarm consensus [%s]",
                    consensus.quorum_str,
                )
            except Exception as exc:
                # 합의 실패 시 안전하게 신호 차단 (fail-closed)
                logger.error("Swarm consensus error — signal blocked: %s", exc)
                return None

        # 주문 실행
        if signal.side == "buy":
            trade_result = await broker.buy(signal.symbol, signal.amount)
        else:
            trade_result = await broker.sell(signal.symbol, signal.amount)

        if not trade_result.success:
            logger.error("Trade failed: %s", trade_result.error)
            return None

        # DB 기록
        pnl = None
        pnl_pct = None
        if signal.side == "sell" and trade_result.amount > 0:
            # 매도 시 P&L은 별도 계산 필요 (평균 매수가 대비)
            pass

        trade_id = await self._trade_repo.insert_trade(
            strategy_id=strategy.id,
            broker=strategy.broker,
            side=signal.side,
            symbol=signal.symbol,
            amount=trade_result.amount,
            price=trade_result.price,
            volume=trade_result.volume,
            fee=trade_result.fee,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )

        result = {
            "trade_id": trade_id,
            "strategy": strategy.id,
            "signal": signal,
            "result": trade_result,
        }

        # 알림 전송
        if self._notify:
            await self._notify(result)

        logger.info(
            "Trade executed: %s %s %s amount=%.0f price=%.2f",
            strategy.id,
            signal.side,
            signal.symbol,
            trade_result.amount,
            trade_result.price,
        )
        return result

    async def execute_manual_trade(
        self,
        broker_name: str,
        side: str,
        symbol: str,
        amount: float,
    ) -> dict[str, Any]:
        """수동 매매 실행 (/confirm 후 호출)"""
        broker = self._brokers.get(broker_name)
        if not broker:
            return {"success": False, "error": f"Unknown broker: {broker_name}"}

        if side == "buy":
            result = await broker.buy(symbol, amount)
        else:
            result = await broker.sell(symbol, amount)

        if result.success:
            await self._trade_repo.insert_trade(
                strategy_id="manual",
                broker=broker_name,
                side=side,
                symbol=symbol,
                amount=result.amount,
                price=result.price,
                volume=result.volume,
                fee=result.fee,
            )

        return {
            "success": result.success,
            "result": result,
            "error": result.error,
        }
