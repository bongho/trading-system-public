"""SwarmConsensus 단위 테스트 — 3-에이전트 병렬 신호 합의."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from src.agents.backend import AgentBackend
from src.agents.models import AgentContext
from src.agents.swarm import SwarmConsensus
from src.strategies.base import TradeSignal

APPROVE = json.dumps({"vote": "approve", "confidence": 0.8, "reasoning": "기술적 지표 강세"})
REJECT = json.dumps({"vote": "reject", "confidence": 0.7, "reasoning": "리스크 과다"})


def _make_swarm(responses: list) -> tuple[SwarmConsensus, AsyncMock]:
    backend = AsyncMock(spec=AgentBackend)
    backend._call.side_effect = responses
    swarm = SwarmConsensus(backend=backend, quorum=2, min_signal_confidence=0.4)
    return swarm, backend


def _signal(confidence: float = 0.8) -> TradeSignal:
    return TradeSignal(
        side="buy",
        symbol="005930",
        amount=100_000,
        confidence=confidence,
        reason="test signal",
    )


def _ctx() -> AgentContext:
    return AgentContext(market_data={"price": 70_000})


class TestSwarmConsensus:
    @pytest.mark.asyncio
    async def test_all_approve(self) -> None:
        """3개 에이전트 모두 approve → 합의 통과."""
        swarm, _ = _make_swarm([APPROVE, APPROVE, APPROVE])
        result = await swarm.evaluate(_signal(), _ctx())
        assert result.approved is True
        assert result.approve_count == 3
        assert len(result.votes) == 3

    @pytest.mark.asyncio
    async def test_minority_approve_rejected(self) -> None:
        """1개만 approve (2개 reject) → quorum 미달, 차단."""
        swarm, _ = _make_swarm([APPROVE, REJECT, REJECT])
        result = await swarm.evaluate(_signal(), _ctx())
        assert result.approved is False
        assert result.approve_count == 1

    @pytest.mark.asyncio
    async def test_one_agent_exception_abstain(self) -> None:
        """1개 에이전트 Exception → abstain 처리, 나머지 2개 approve → 합의 통과."""
        swarm, _ = _make_swarm([APPROVE, Exception("timeout"), APPROVE])
        result = await swarm.evaluate(_signal(), _ctx())
        assert result.approved is True
        # abstain 에이전트는 vote="abstain"
        abstain_votes = [v for v in result.votes if v.vote == "abstain"]
        assert len(abstain_votes) == 1

    @pytest.mark.asyncio
    async def test_low_confidence_blocked_immediately(self) -> None:
        """signal.confidence < min_confidence → _call 호출 없이 즉시 차단."""
        swarm, backend = _make_swarm([])
        result = await swarm.evaluate(_signal(confidence=0.2), _ctx())
        assert result.approved is False
        backend._call.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_agents_fail_closed(self) -> None:
        """3개 모두 Exception → abstain, quorum 0 → fail-closed."""
        swarm, _ = _make_swarm([
            Exception("network error"),
            Exception("timeout"),
            Exception("parse error"),
        ])
        result = await swarm.evaluate(_signal(), _ctx())
        assert result.approved is False
        assert result.approve_count == 0
        assert all(v.vote == "abstain" for v in result.votes)

    @pytest.mark.asyncio
    async def test_quorum_str_format(self) -> None:
        """ConsensusResult.quorum_str 형식 검증."""
        swarm, _ = _make_swarm([APPROVE, APPROVE, REJECT])
        result = await swarm.evaluate(_signal(), _ctx())
        assert result.approved is True
        assert result.quorum_str == "2/3"

    @pytest.mark.asyncio
    async def test_summary_contains_verdict(self) -> None:
        """summary에 종목·방향·판정 포함."""
        swarm, _ = _make_swarm([APPROVE, REJECT, REJECT])
        result = await swarm.evaluate(_signal(), _ctx())
        assert "005930" in result.summary
        assert "BUY" in result.summary
        assert "차단" in result.summary
