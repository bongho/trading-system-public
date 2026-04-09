"""에이전트 데이터 모델 테스트."""

from __future__ import annotations

from src.agents.models import AgentContext, AnalysisResult, OptimizeResult, ReviewResult


class TestAnalysisResult:
    def test_to_dict(self) -> None:
        result = AnalysisResult(
            symbol="KRW-BTC",
            sentiment="bullish",
            confidence=0.85,
            summary="강세 전환 신호",
            indicators={"rsi": 35.0, "bb_position": 0.2},
            key_levels={"support": 90000000, "resistance": 95000000},
        )
        d = result.to_dict()
        assert d["symbol"] == "KRW-BTC"
        assert d["sentiment"] == "bullish"
        assert d["confidence"] == 0.85
        assert d["indicators"]["rsi"] == 35.0
        assert d["key_levels"]["support"] == 90000000

    def test_defaults(self) -> None:
        result = AnalysisResult(
            symbol="KRW-ETH",
            sentiment="neutral",
            confidence=0.5,
            summary="횡보",
        )
        assert result.indicators == {}
        assert result.key_levels == {}


class TestOptimizeResult:
    def test_to_dict(self) -> None:
        result = OptimizeResult(
            strategy_id="simple_rsi",
            param_changes={"rsi_period": 21},
            expected_improvement="승률 5% 향상 예상",
            rationale="RSI 기간 확장으로 노이즈 감소",
        )
        d = result.to_dict()
        assert d["strategy_id"] == "simple_rsi"
        assert d["param_changes"] == {"rsi_period": 21}
        assert d["code_diff"] is None

    def test_with_code_diff(self) -> None:
        result = OptimizeResult(
            strategy_id="double_bb_short",
            code_diff="# modified code",
        )
        assert result.code_diff == "# modified code"
        assert result.to_dict()["code_diff"] == "# modified code"


class TestReviewResult:
    def test_approved(self) -> None:
        result = ReviewResult(approved=True, risk_score=0.2)
        d = result.to_dict()
        assert d["approved"] is True
        assert d["risk_score"] == 0.2
        assert d["concerns"] == []

    def test_rejected_with_feedback(self) -> None:
        result = ReviewResult(
            approved=False,
            risk_score=0.8,
            concerns=["과도한 파라미터 변경", "백테스트 기간 부족"],
            feedback="RSI 기간을 14→28로 바꾸면 과적합 위험",
        )
        assert not result.approved
        assert len(result.concerns) == 2
        assert "과적합" in result.feedback


class TestAgentContext:
    def test_defaults(self) -> None:
        ctx = AgentContext()
        assert ctx.market_data == {}
        assert ctx.strategy_performance == {}
        assert ctx.current_params == {}
        assert ctx.backtest_result is None
        assert ctx.analysis is None

    def test_with_analysis(self) -> None:
        analysis = AnalysisResult(
            symbol="KRW-BTC", sentiment="bearish", confidence=0.7, summary="하락"
        )
        ctx = AgentContext(analysis=analysis)
        assert ctx.analysis.sentiment == "bearish"
