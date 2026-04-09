"""에이전트 데이터 모델 — 백엔드 독립적, JSON 직렬화 가능."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class AnalysisResult:
    """Analyst 페르소나 출력"""

    symbol: str
    sentiment: Literal["bullish", "bearish", "neutral"]
    confidence: float  # 0.0 ~ 1.0
    summary: str
    indicators: dict[str, float] = field(default_factory=dict)
    key_levels: dict[str, float] = field(default_factory=dict)  # support, resistance

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "summary": self.summary,
            "indicators": self.indicators,
            "key_levels": self.key_levels,
        }


@dataclass
class OptimizeResult:
    """Strategist 페르소나 출력"""

    strategy_id: str
    param_changes: dict[str, Any] = field(default_factory=dict)
    code_diff: str | None = None  # sandbox에서 수정된 코드
    expected_improvement: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "param_changes": self.param_changes,
            "code_diff": self.code_diff,
            "expected_improvement": self.expected_improvement,
            "rationale": self.rationale,
        }


@dataclass
class ReviewResult:
    """Reviewer 페르소나 출력"""

    approved: bool
    risk_score: float  # 0.0 (안전) ~ 1.0 (위험)
    concerns: list[str] = field(default_factory=list)
    feedback: str = ""  # 거부 시 Strategist에 전달할 피드백

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "risk_score": self.risk_score,
            "concerns": self.concerns,
            "feedback": self.feedback,
        }


@dataclass
class AgentContext:
    """에이전트 실행에 필요한 컨텍스트"""

    market_data: dict[str, Any] = field(default_factory=dict)
    strategy_performance: dict[str, Any] = field(default_factory=dict)
    current_params: dict[str, Any] = field(default_factory=dict)
    backtest_result: dict[str, Any] | None = None
    analysis: AnalysisResult | None = None
