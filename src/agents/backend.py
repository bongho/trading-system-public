"""AgentBackend ABC — Claude 직접 호출과 OpenClaw 연동의 공통 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.agents.models import AgentContext, AnalysisResult, OptimizeResult, ReviewResult


class AgentBackend(ABC):
    """AI 에이전트 실행 백엔드 추상화.

    Phase 5: ClaudeDirectBackend (anthropic SDK)
    Phase 6: OpenClawBackend (WebSocket RPC)
    """

    @abstractmethod
    async def analyze(self, symbol: str, ctx: AgentContext) -> AnalysisResult:
        """시장 분석 (Analyst 페르소나)"""

    @abstractmethod
    async def optimize(
        self, strategy_id: str, ctx: AgentContext
    ) -> OptimizeResult:
        """전략 최적화 제안 (Strategist 페르소나)"""

    @abstractmethod
    async def review(
        self, proposal: OptimizeResult, ctx: AgentContext
    ) -> ReviewResult:
        """변경안 검토 (Reviewer 페르소나)"""

    async def close(self) -> None:
        """리소스 정리"""
