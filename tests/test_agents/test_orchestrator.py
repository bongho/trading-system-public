"""AgentOrchestrator 테스트 — mock backend 사용."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.backend import AgentBackend
from src.agents.models import (
    AgentContext,
    AnalysisResult,
    OptimizeResult,
    ReviewResult,
)
from src.agents.orchestrator import AgentOrchestrator, OrchestratorResult
from src.agents.sandbox import Sandbox
from src.strategies.base import BacktestResult


def _make_mock_backend(
    *,
    analysis: AnalysisResult | None = None,
    optimize: OptimizeResult | None = None,
    review: ReviewResult | None = None,
) -> AgentBackend:
    backend = AsyncMock(spec=AgentBackend)
    backend.analyze.return_value = analysis or AnalysisResult(
        symbol="KRW-BTC", sentiment="bullish", confidence=0.8, summary="테스트"
    )
    backend.optimize.return_value = optimize or OptimizeResult(
        strategy_id="simple_rsi",
        param_changes={"rsi_period": 21},
        expected_improvement="5% 향상",
        rationale="테스트",
    )
    backend.review.return_value = review or ReviewResult(
        approved=True, risk_score=0.2
    )
    return backend


def _make_mock_strategy():
    strategy = MagicMock()
    strategy.id = "simple_rsi"
    strategy.name = "Simple RSI"
    strategy.broker = "upbit"
    strategy.symbols = ["KRW-BTC"]
    strategy.capital_allocation = 100000
    strategy.params = {"rsi_period": 14}
    strategy.backtest = AsyncMock(return_value=BacktestResult())
    return strategy


def _make_mock_registry(strategy=None):
    registry = MagicMock()
    s = strategy or _make_mock_strategy()
    registry.get.return_value = s
    registry.get_enabled.return_value = [s]
    return registry


def _make_orchestrator(backend=None, registry=None):
    return AgentOrchestrator(
        backend=backend or _make_mock_backend(),
        registry=registry or _make_mock_registry(),
        trade_repo=AsyncMock(),
        brokers={},
        sandbox=Sandbox(),
    )


class TestOrchestratorAnalyze:
    @pytest.mark.asyncio
    async def test_analyze_returns_result(self) -> None:
        backend = _make_mock_backend()
        orch = _make_orchestrator(backend=backend)
        result = await orch.analyze("KRW-BTC")
        assert result.symbol == "KRW-BTC"
        assert result.sentiment == "bullish"
        backend.analyze.assert_called_once()


class TestOrchestratorOptimize:
    @pytest.mark.asyncio
    async def test_optimize_approved_first_round(self) -> None:
        backend = _make_mock_backend()
        orch = _make_orchestrator(backend=backend)
        result = await orch.optimize("simple_rsi")

        assert result.success
        assert result.rounds == 1
        assert result.proposal.param_changes == {"rsi_period": 21}
        # pending에 저장됨
        assert result.session_id in orch.get_pending_proposals()

    @pytest.mark.asyncio
    async def test_optimize_rejected_then_approved(self) -> None:
        backend = _make_mock_backend()
        # 첫 라운드 거부, 두 번째 승인
        backend.review.side_effect = [
            ReviewResult(approved=False, risk_score=0.7, feedback="위험"),
            ReviewResult(approved=True, risk_score=0.3),
        ]
        orch = _make_orchestrator(backend=backend)
        result = await orch.optimize("simple_rsi")

        assert result.success
        assert result.rounds == 2

    @pytest.mark.asyncio
    async def test_optimize_all_rounds_rejected(self) -> None:
        backend = _make_mock_backend(
            review=ReviewResult(approved=False, risk_score=0.9, feedback="위험")
        )
        orch = _make_orchestrator(backend=backend)
        result = await orch.optimize("simple_rsi")

        assert not result.success
        assert result.rounds == 3
        assert "3회" in result.error

    @pytest.mark.asyncio
    async def test_optimize_no_changes(self) -> None:
        backend = _make_mock_backend(
            optimize=OptimizeResult(strategy_id="simple_rsi")
        )
        orch = _make_orchestrator(backend=backend)
        result = await orch.optimize("simple_rsi")

        assert result.success
        assert "변경사항 없음" in result.error

    @pytest.mark.asyncio
    async def test_optimize_strategy_not_found(self) -> None:
        registry = MagicMock()
        registry.get.return_value = None
        orch = _make_orchestrator(registry=registry)
        result = await orch.optimize("nonexistent")

        assert not result.success
        assert "찾을 수 없음" in result.error


class TestOrchestratorConfirmCancel:
    @pytest.mark.asyncio
    async def test_confirm_applies_param_changes(self) -> None:
        backend = _make_mock_backend()
        registry = _make_mock_registry()
        orch = _make_orchestrator(backend=backend, registry=registry)

        result = await orch.optimize("simple_rsi")
        assert result.success

        applied = await orch.confirm_proposal(result.session_id)
        assert applied
        registry.update_params.assert_called_once_with(
            "simple_rsi", {"rsi_period": 21}
        )
        # pending에서 제거됨
        assert result.session_id not in orch.get_pending_proposals()

    @pytest.mark.asyncio
    async def test_confirm_unknown_session(self) -> None:
        orch = _make_orchestrator()
        applied = await orch.confirm_proposal("unknown_id")
        assert not applied

    def test_cancel_proposal(self) -> None:
        orch = _make_orchestrator()
        # 수동으로 pending에 추가
        orch._pending_proposals["test_sid"] = OrchestratorResult(
            session_id="test_sid", success=True
        )
        assert orch.cancel_proposal("test_sid")
        assert "test_sid" not in orch.get_pending_proposals()

    def test_cancel_unknown(self) -> None:
        orch = _make_orchestrator()
        assert not orch.cancel_proposal("nope")


class TestOrchestratorReview:
    @pytest.mark.asyncio
    async def test_review_strategy(self) -> None:
        backend = _make_mock_backend(
            review=ReviewResult(approved=True, risk_score=0.15)
        )
        orch = _make_orchestrator(backend=backend)
        result = await orch.review_strategy("simple_rsi")
        assert result.approved
        assert result.risk_score == 0.15

    @pytest.mark.asyncio
    async def test_review_not_found(self) -> None:
        registry = MagicMock()
        registry.get.return_value = None
        orch = _make_orchestrator(registry=registry)
        result = await orch.review_strategy("bad_id")
        assert not result.approved
        assert result.risk_score == 1.0
