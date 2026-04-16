"""Evaluator-Optimizer 오케스트레이터 — 백엔드 독립적 멀티에이전트 루프."""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agents.backend import AgentBackend
from src.agents.models import AgentContext, AnalysisResult, OptimizeResult, ReviewResult
from src.agents.sandbox import Sandbox
from src.brokers.base import BrokerAdapter
from src.data.collector import MarketDataCollector
from src.db.repository import TradeRepository
from src.strategies.base import BacktestResult
from src.strategies.registry import StrategyRegistry

logger = logging.getLogger(__name__)

MAX_OPTIMIZE_ROUNDS = 3


@dataclass
class OrchestratorResult:
    """오케스트레이션 실행 결과"""

    session_id: str
    success: bool
    analysis: AnalysisResult | None = None
    proposal: OptimizeResult | None = None
    review: ReviewResult | None = None
    rounds: int = 0
    applied: bool = False
    error: str = ""
    log: list[dict[str, Any]] = field(default_factory=list)


class AgentOrchestrator:
    """Evaluator-Optimizer 패턴 오케스트레이터.

    흐름:
    1. Analyst: 시장 분석
    2. Strategist: 최적화 제안
    3. Reviewer: 검토 (승인/거부)
    4. 거부 시 → Strategist에 피드백 → 재생성 (최대 3회)
    5. 승인 시 → sandbox 백테스트 → 검증 → 적용 대기
    """

    def __init__(
        self,
        backend: AgentBackend,
        registry: StrategyRegistry,
        trade_repo: TradeRepository,
        brokers: dict[str, BrokerAdapter],
        sandbox: Sandbox | None = None,
        collector: MarketDataCollector | None = None,
    ) -> None:
        self._backend = backend
        self._registry = registry
        self._trade_repo = trade_repo
        self._brokers = brokers
        self._sandbox = sandbox or Sandbox()
        self._collector = collector
        # 승인 대기 중인 제안 (Telegram /confirm 연동)
        self._pending_proposals: dict[str, OrchestratorResult] = {}
        # 신규 전략 생성 대기 (Telegram /strategy new → /ai confirm)
        self._pending_new_strategies: dict[str, dict[str, Any]] = {}

    async def analyze(self, symbol: str) -> AnalysisResult:
        """단독 분석 실행 (/ai analyze)"""
        ctx = await self._build_context(symbol=symbol)
        return await self._backend.analyze(symbol, ctx)

    async def optimize(
        self,
        strategy_id: str,
        notify_callback: Any | None = None,
    ) -> OrchestratorResult:
        """전체 Evaluator-Optimizer 루프 실행 (/ai optimize)"""
        session_id = uuid.uuid4().hex[:12]
        result = OrchestratorResult(session_id=session_id, success=False)

        strategy = self._registry.get(strategy_id)
        if not strategy:
            result.error = f"전략을 찾을 수 없음: {strategy_id}"
            return result

        # 1. 컨텍스트 구성
        ctx = await self._build_context(
            strategy_id=strategy_id,
            symbol=strategy.symbols[0] if strategy.symbols else None,
        )
        ctx.current_params = strategy.params

        # 2. Analyst 분석
        if notify_callback:
            await notify_callback("🔍 [1/3] 시장 분석 중...")
        analysis = await self._backend.analyze(
            strategy.symbols[0] if strategy.symbols else "KRW-BTC", ctx
        )
        result.analysis = analysis
        ctx.analysis = analysis
        result.log.append({"role": "analyst", "output": analysis.to_dict()})

        # 3. Strategist-Reviewer 루프
        for round_num in range(1, MAX_OPTIMIZE_ROUNDS + 1):
            result.rounds = round_num
            if notify_callback:
                await notify_callback(
                    f"💡 [2/3] 전략 최적화 중... (Round {round_num}/{MAX_OPTIMIZE_ROUNDS})"
                )

            # Strategist 제안
            proposal = await self._backend.optimize(strategy_id, ctx)
            result.proposal = proposal
            result.log.append({"role": "strategist", "round": round_num, "output": proposal.to_dict()})

            if not proposal.param_changes and not proposal.code_diff:
                result.error = "Strategist가 변경사항 없음으로 판단"
                result.success = True
                return result

            # 제안된 파라미터로 백테스트
            backtest_after = await self._run_proposal_backtest(
                strategy, proposal, ctx
            )
            ctx.backtest_result = self._backtest_to_dict(backtest_after)

            # Reviewer 검토
            if notify_callback:
                await notify_callback("🔎 [3/3] 변경안 검토 중...")
            review = await self._backend.review(proposal, ctx)
            result.review = review
            result.log.append({"role": "reviewer", "round": round_num, "output": review.to_dict()})

            if review.approved:
                result.success = True
                # pending에 저장 → Telegram /confirm 대기
                self._pending_proposals[session_id] = result
                return result

            # 거부 시 피드백을 컨텍스트에 추가
            ctx.strategy_performance["reviewer_feedback"] = review.feedback
            ctx.strategy_performance["reviewer_concerns"] = review.concerns
            logger.info(
                "Round %d rejected (risk=%.2f): %s",
                round_num, review.risk_score, review.feedback,
            )

        result.error = f"{MAX_OPTIMIZE_ROUNDS}회 시도 후에도 Reviewer 승인 실패"
        return result

    async def review_strategy(self, strategy_id: str) -> ReviewResult:
        """현재 전략 성과만 검토 (/ai review)"""
        strategy = self._registry.get(strategy_id)
        if not strategy:
            return ReviewResult(
                approved=False, risk_score=1.0,
                concerns=[f"전략 없음: {strategy_id}"],
            )

        ctx = await self._build_context(strategy_id=strategy_id)
        ctx.current_params = strategy.params

        # 현재 상태를 "제안"으로 감싸서 review
        current_proposal = OptimizeResult(
            strategy_id=strategy_id,
            rationale="현재 전략 상태 검토",
        )
        return await self._backend.review(current_proposal, ctx)

    async def confirm_proposal(self, session_id: str) -> bool:
        """Telegram /confirm 후 승인된 제안 적용."""
        result = self._pending_proposals.pop(session_id, None)
        if not result or not result.proposal:
            return False

        strategy = self._registry.get(result.proposal.strategy_id)
        if not strategy:
            return False

        proposal = result.proposal

        # 파라미터 변경 적용
        if proposal.param_changes:
            self._registry.update_params(strategy.id, proposal.param_changes)
            logger.info(
                "Applied param changes to %s: %s",
                strategy.id, proposal.param_changes,
            )

        # 코드 변경 적용 (sandbox → strategies)
        if proposal.code_diff:
            self._sandbox.apply_code_diff(strategy.id, proposal.code_diff)
            # 백테스트 비교 후 프로모션
            before = await self._run_current_backtest(strategy)
            after_path = self._sandbox._sandbox / f"{strategy.id}.py"
            if after_path.exists():
                promoted = await self._sandbox.validate_and_promote(
                    strategy.id, before, BacktestResult()
                )
                if promoted:
                    self._registry.reload_strategy(
                        strategy.id, f"src.strategies.{strategy.id}"
                    )
                    logger.info("Code change promoted for %s", strategy.id)
                else:
                    logger.warning("Code change validation failed for %s", strategy.id)
                    return False

        result.applied = True
        return True

    def cancel_proposal(self, session_id: str) -> bool:
        """제안 취소 (최적화 제안 또는 신규 전략 모두 처리)."""
        if self._pending_proposals.pop(session_id, None) is not None:
            return True
        meta = self._pending_new_strategies.pop(session_id, None)
        if meta:
            # sandbox 파일 정리
            self._sandbox.cleanup(meta["strategy_id"])
            return True
        return False

    def get_pending_proposals(self) -> dict[str, OrchestratorResult]:
        """대기 중인 최적화 제안 목록."""
        return dict(self._pending_proposals)

    def get_pending_new_strategies(self) -> dict[str, dict[str, Any]]:
        """대기 중인 신규 전략 목록."""
        return dict(self._pending_new_strategies)

    async def create_new_strategy(
        self,
        description: str,
        capital_allocation: float = 100000,
        notify_callback: Any | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """자연어 설명으로 전략 생성 → sandbox 백테스트 → 승인 대기.

        Returns:
            (session_id, metadata) — metadata에 backtest 결과 포함
        """
        session_id = uuid.uuid4().hex[:12]

        if notify_callback:
            await notify_callback("🤖 전략 코드 생성 중...")

        # 1. Claude로 코드 생성
        raw = await self._backend.generate_strategy(description)
        strategy_id = raw["strategy_id"]
        code = raw["code"]

        # 2. sandbox에 코드 저장
        sandbox_path = self._sandbox._sandbox / f"{strategy_id}.py"
        sandbox_path.write_text(code, encoding="utf-8")

        # 3. syntax 검증 + create_strategy() 존재 확인
        spec = importlib.util.spec_from_file_location(
            f"sandbox_{strategy_id}", str(sandbox_path)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # SyntaxError 시 여기서 발생

        if not hasattr(module, "create_strategy"):
            sandbox_path.unlink(missing_ok=True)
            raise AttributeError(f"create_strategy() not found in generated code")

        # 4. 백테스트
        if notify_callback:
            await notify_callback("📊 백테스트 실행 중 (30일)...")

        alloc = float(raw.get("capital_allocation", capital_allocation))
        strategy_obj = module.create_strategy(capital_allocation=alloc)

        symbol = raw["symbols"][0] if raw.get("symbols") else "KRW-BTC"
        broker_name = raw.get("broker", "upbit")
        data = await self._get_historical_data(symbol, broker_name, days=30)

        backtest_result = BacktestResult()
        if data:
            try:
                backtest_result = await strategy_obj.backtest({symbol: data})
            except Exception as e:
                logger.warning("Backtest failed for new strategy %s: %s", strategy_id, e)

        # 5. pending에 저장
        meta: dict[str, Any] = {
            "session_id": session_id,
            "description": description,
            "strategy_id": strategy_id,
            "strategy_name": raw.get("strategy_name", strategy_id),
            "broker": broker_name,
            "symbols": raw.get("symbols", [symbol]),
            "interval_minutes": int(raw.get("interval_minutes", 5)),
            "capital_allocation": alloc,
            "code": code,
            "backtest": self._backtest_to_dict(backtest_result),
        }
        self._pending_new_strategies[session_id] = meta

        return session_id, meta

    async def confirm_new_strategy(self, session_id: str) -> dict[str, Any] | None:
        """신규 전략 승인 — sandbox 코드를 src/strategies/ 에 복사.

        Returns:
            strategy metadata (caller가 registry/repo/scheduler 등록 책임)
            None: session_id 없음
        """
        meta = self._pending_new_strategies.pop(session_id, None)
        if not meta:
            return None

        strategy_id = meta["strategy_id"]
        sandbox_path = self._sandbox._sandbox / f"{strategy_id}.py"
        dest = self._sandbox._strategies / f"{strategy_id}.py"

        if sandbox_path.exists():
            shutil.copy2(sandbox_path, dest)
            sandbox_path.unlink(missing_ok=True)
        else:
            # sandbox 파일이 이미 없으면 code에서 재생성
            dest.write_text(meta["code"], encoding="utf-8")

        logger.info("New strategy promoted: %s -> %s", strategy_id, dest)
        return meta

    async def _build_context(
        self,
        strategy_id: str | None = None,
        symbol: str | None = None,
    ) -> AgentContext:
        """에이전트 실행 컨텍스트 구성."""
        ctx = AgentContext()

        # 시장 데이터 수집
        if symbol:
            for broker in self._brokers.values():
                try:
                    data = await broker.get_market_data(symbol, "5m", 200)
                    ctx.market_data[symbol] = {
                        "candles": len(data),
                        "last_close": data[-1].close if data else 0,
                        "last_high": data[-1].high if data else 0,
                        "last_low": data[-1].low if data else 0,
                        "last_volume": data[-1].volume if data else 0,
                    }
                    # 기술 지표 요약
                    if len(data) >= 20:
                        from src.utils.indicators import (
                            bollinger_bands,
                            rsi,
                            squeeze_momentum,
                            to_dataframe,
                        )

                        df = to_dataframe(data)
                        rsi_val = rsi(df["close"])
                        bb_upper, bb_mid, bb_lower = bollinger_bands(df["close"])
                        sq_on, momentum = squeeze_momentum(
                            df["high"], df["low"], df["close"]
                        )
                        ctx.market_data[symbol]["indicators"] = {
                            "rsi": round(float(rsi_val.iloc[-1]), 2) if not rsi_val.isna().iloc[-1] else None,
                            "bb_upper": round(float(bb_upper.iloc[-1]), 2) if not bb_upper.isna().iloc[-1] else None,
                            "bb_mid": round(float(bb_mid.iloc[-1]), 2) if not bb_mid.isna().iloc[-1] else None,
                            "bb_lower": round(float(bb_lower.iloc[-1]), 2) if not bb_lower.isna().iloc[-1] else None,
                            "squeeze_on": bool(sq_on.iloc[-1]) if not sq_on.isna().iloc[-1] else None,
                            "momentum": round(float(momentum.iloc[-1]), 4) if not momentum.isna().iloc[-1] else None,
                        }
                    break  # 첫 브로커에서 성공하면 중단
                except Exception as e:
                    logger.warning("Failed to get market data for %s: %s", symbol, e)

        # 전략 성과 데이터
        if strategy_id:
            stats = await self._trade_repo.get_strategy_stats(strategy_id)
            ctx.strategy_performance = stats

        return ctx

    async def _get_historical_data(
        self, symbol: str, broker_name: str, days: int = 30
    ) -> list:
        """캔들 데이터 조회 — collector 우선, fallback으로 broker 직접 호출."""
        if self._collector:
            return await self._collector.get_candles(
                symbol, "5m", days=days, broker_name=broker_name
            )

        broker = self._brokers.get(broker_name)
        if not broker:
            return []
        if hasattr(broker, "get_historical_data"):
            return await broker.get_historical_data(symbol, "5m", days)
        return await broker.get_market_data(symbol, "5m", 200)

    async def _run_proposal_backtest(
        self,
        strategy: Any,
        proposal: OptimizeResult,
        ctx: AgentContext,
    ) -> BacktestResult:
        """제안된 변경으로 백테스트 실행."""
        try:
            test_params = dict(strategy.params)
            test_params.update(proposal.param_changes)

            symbol = strategy.symbols[0] if strategy.symbols else None
            if not symbol:
                return BacktestResult()

            data = await self._get_historical_data(symbol, strategy.broker)
            if not data:
                return BacktestResult()

            import importlib

            mod = importlib.import_module(f"src.strategies.{strategy.id}")
            temp_strategy = mod.create_strategy(
                capital_allocation=strategy.capital_allocation,
                params=test_params,
            )
            return await temp_strategy.backtest({symbol: data})

        except Exception as e:
            logger.error("Proposal backtest failed: %s", e)
            return BacktestResult()

    async def _run_current_backtest(self, strategy: Any) -> BacktestResult:
        """현재 전략으로 백테스트 실행 (비교 기준)."""
        try:
            symbol = strategy.symbols[0] if strategy.symbols else None
            if not symbol:
                return BacktestResult()

            data = await self._get_historical_data(symbol, strategy.broker)
            if not data:
                return BacktestResult()

            return await strategy.backtest({symbol: data})
        except Exception as e:
            logger.error("Current backtest failed: %s", e)
            return BacktestResult()

    @staticmethod
    def _backtest_to_dict(result: BacktestResult) -> dict[str, Any]:
        return {
            "total_trades": result.total_trades,
            "win_count": result.win_count,
            "loss_count": result.loss_count,
            "total_pnl": result.total_pnl,
            "total_pnl_pct": result.total_pnl_pct,
            "max_drawdown": result.max_drawdown,
            "sharpe_ratio": result.sharpe_ratio,
            "win_rate": result.win_rate,
        }
