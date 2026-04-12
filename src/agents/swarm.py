"""Swarm Consensus — 멀티에이전트 매매 신호 합의 시스템.

3개 역할 에이전트(기술적 분석 / 리스크 가드 / 컨트라리언)가
독립적으로 신호를 평가하고 2/3 다수결로 최종 승인/거부를 결정한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from src.agents.backend import AgentBackend
from src.agents.models import AgentContext, ConsensusResult, SignalVote
from src.strategies.base import TradeSignal

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_ROLES = ("technical", "risk_guard", "contrarian")
_QUORUM = 2  # 3개 중 2개 이상 approve 시 실행


def _load_prompt(role: str) -> str:
    return (_PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 블록 추출 (claude_backend와 동일 패턴)."""
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return json.loads(text[start:end].strip())
    if "{" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    return json.loads(text.strip())


class SwarmConsensus:
    """3-에이전트 신호 합의 시스템.

    Usage:
        swarm = SwarmConsensus(backend)
        result = await swarm.evaluate(signal, ctx)
        if not result.approved:
            return  # 신호 차단
    """

    def __init__(
        self,
        backend: AgentBackend,
        quorum: int = _QUORUM,
        min_signal_confidence: float = 0.4,
    ) -> None:
        """
        Args:
            backend: AI 백엔드 (Claude / OpenAI)
            quorum: 승인에 필요한 최소 approve 수 (기본 2/3)
            min_signal_confidence: 이 값 미만의 신호는 consensus 없이 차단
        """
        self._backend = backend
        self._quorum = quorum
        self._min_confidence = min_signal_confidence
        self._prompts = {role: _load_prompt(role) for role in _ROLES}

    async def evaluate(
        self,
        signal: TradeSignal,
        ctx: AgentContext,
    ) -> ConsensusResult:
        """3개 에이전트를 병렬 실행하여 합의 결과를 반환한다.

        신호 confidence가 min_signal_confidence 미만이면 즉시 거부.
        에이전트 호출 실패 시 해당 에이전트는 abstain 처리.
        """
        # 낮은 confidence 신호는 즉시 차단 (API 비용 절약)
        if signal.confidence < self._min_confidence:
            return ConsensusResult(
                approved=False,
                summary=f"신호 confidence({signal.confidence:.2f}) < 임계값({self._min_confidence:.2f}) — 합의 없이 차단",
            )

        user_msg = self._build_user_message(signal, ctx)

        # 3개 에이전트 병렬 호출
        tasks = [
            self._vote(role, user_msg)
            for role in _ROLES
        ]
        raw_votes = await asyncio.gather(*tasks, return_exceptions=True)

        votes: list[SignalVote] = []
        for role, raw in zip(_ROLES, raw_votes):
            if isinstance(raw, Exception):
                logger.warning("Swarm agent [%s] failed: %s", role, raw)
                votes.append(SignalVote(
                    agent_role=role,
                    vote="abstain",
                    confidence=0.0,
                    reasoning=f"에이전트 호출 실패: {raw}",
                ))
            else:
                votes.append(raw)

        approve_count = sum(1 for v in votes if v.vote == "approve")
        approved = approve_count >= self._quorum

        summary = self._build_summary(signal, votes, approved)
        result = ConsensusResult(approved=approved, votes=votes, summary=summary)

        logger.info(
            "Swarm consensus [%s %s]: %s (%s) — %s",
            signal.side.upper(),
            signal.symbol,
            "APPROVED" if approved else "REJECTED",
            result.quorum_str,
            summary,
        )
        return result

    async def _vote(self, role: str, user_msg: str) -> SignalVote:
        """단일 에이전트 호출 → SignalVote 반환."""
        system_prompt = self._prompts[role]

        raw = await self._backend._call(system_prompt, user_msg, max_tokens=512)

        data = _extract_json(raw)
        vote_val = data.get("vote", "abstain")
        if vote_val not in ("approve", "reject", "abstain"):
            vote_val = "abstain"

        return SignalVote(
            agent_role=role,
            vote=vote_val,
            confidence=float(data.get("confidence", 0.5)),
            reasoning=str(data.get("reasoning", "")),
        )

    @staticmethod
    def _build_user_message(signal: TradeSignal, ctx: AgentContext) -> str:
        signal_dict = {
            "side": signal.side,
            "symbol": signal.symbol,
            "amount": signal.amount,
            "confidence": signal.confidence,
            "reason": signal.reason,
        }
        return (
            f"## Proposed Signal\n```json\n{json.dumps(signal_dict, ensure_ascii=False)}\n```\n\n"
            f"## Market Data\n```json\n{json.dumps(ctx.market_data, default=str, ensure_ascii=False)}\n```\n\n"
            f"Respond with JSON only."
        )

    @staticmethod
    def _build_summary(
        signal: TradeSignal,
        votes: list[SignalVote],
        approved: bool,
    ) -> str:
        lines = []
        for v in votes:
            emoji = {"approve": "✅", "reject": "❌", "abstain": "⚪"}.get(v.vote, "⚪")
            lines.append(f"{emoji} [{v.agent_role}] {v.reasoning}")
        verdict = "실행" if approved else "차단"
        header = f"{signal.side.upper()} {signal.symbol} → {verdict}"
        return header + "\n" + "\n".join(lines)
