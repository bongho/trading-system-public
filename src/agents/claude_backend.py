"""Claude API 직접 호출 백엔드 — Phase 5 기본 구현."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anthropic

from src.agents.backend import AgentBackend
from src.agents.models import AgentContext, AnalysisResult, OptimizeResult, ReviewResult

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 블록 추출"""
    # ```json ... ``` 블록 우선
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return json.loads(text[start:end].strip())
    # ```{ ... }``` 블록
    if "```" in text and "{" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    # 텍스트 내 JSON 객체 추출
    if "{" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    # 순수 JSON
    return json.loads(text.strip())


class ClaudeDirectBackend(AgentBackend):
    """anthropic SDK를 통한 Claude API 직접 호출."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def _call(self, system: str, user_message: str, max_tokens: int = 2048) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    async def analyze(self, symbol: str, ctx: AgentContext) -> AnalysisResult:
        system = _load_prompt("analyst")
        user_msg = (
            f"Analyze {symbol}.\n\n"
            f"## Market Data\n```json\n{json.dumps(ctx.market_data, default=str)}\n```\n\n"
            f"## Current Positions\n```json\n{json.dumps(ctx.strategy_performance)}\n```\n\n"
            f"Respond with JSON only."
        )

        try:
            raw = await self._call(system, user_msg)
            data = _extract_json(raw)
            return AnalysisResult(
                symbol=data.get("symbol", symbol),
                sentiment=data.get("sentiment", "neutral"),
                confidence=float(data.get("confidence", 0.5)),
                summary=data.get("summary", ""),
                indicators=data.get("indicators", {}),
                key_levels=data.get("key_levels", {}),
            )
        except Exception as e:
            logger.error("Analyst failed: %s", e)
            return AnalysisResult(
                symbol=symbol,
                sentiment="neutral",
                confidence=0.0,
                summary=f"분석 실패: {e}",
            )

    async def optimize(
        self, strategy_id: str, ctx: AgentContext
    ) -> OptimizeResult:
        system = _load_prompt("strategist")
        user_msg = (
            f"Optimize strategy: {strategy_id}\n\n"
            f"## Current Parameters\n```json\n{json.dumps(ctx.current_params)}\n```\n\n"
            f"## Performance\n```json\n{json.dumps(ctx.strategy_performance)}\n```\n\n"
        )
        if ctx.analysis:
            user_msg += f"## Analyst Assessment\n```json\n{json.dumps(ctx.analysis.to_dict())}\n```\n\n"
        if ctx.backtest_result:
            user_msg += f"## Backtest Result\n```json\n{json.dumps(ctx.backtest_result)}\n```\n\n"
        user_msg += "Respond with JSON only."

        try:
            raw = await self._call(system, user_msg)
            data = _extract_json(raw)
            return OptimizeResult(
                strategy_id=data.get("strategy_id", strategy_id),
                param_changes=data.get("param_changes", {}),
                code_diff=data.get("code_diff"),
                expected_improvement=data.get("expected_improvement", ""),
                rationale=data.get("rationale", ""),
            )
        except Exception as e:
            logger.error("Strategist failed: %s", e)
            return OptimizeResult(
                strategy_id=strategy_id,
                expected_improvement=f"최적화 실패: {e}",
            )

    async def review(
        self, proposal: OptimizeResult, ctx: AgentContext
    ) -> ReviewResult:
        system = _load_prompt("reviewer")
        user_msg = (
            f"Review this proposal.\n\n"
            f"## Proposal\n```json\n{json.dumps(proposal.to_dict())}\n```\n\n"
            f"## Current Parameters\n```json\n{json.dumps(ctx.current_params)}\n```\n\n"
            f"## Performance\n```json\n{json.dumps(ctx.strategy_performance)}\n```\n\n"
        )
        if ctx.backtest_result:
            user_msg += f"## Backtest (After)\n```json\n{json.dumps(ctx.backtest_result)}\n```\n\n"
        user_msg += "Respond with JSON only."

        try:
            raw = await self._call(system, user_msg)
            data = _extract_json(raw)
            return ReviewResult(
                approved=bool(data.get("approved", False)),
                risk_score=float(data.get("risk_score", 0.5)),
                concerns=data.get("concerns", []),
                feedback=data.get("feedback", ""),
            )
        except Exception as e:
            logger.error("Reviewer failed: %s", e)
            return ReviewResult(
                approved=False,
                risk_score=1.0,
                concerns=[f"리뷰 실패: {e}"],
                feedback="시스템 오류로 자동 거부",
            )

    async def close(self) -> None:
        await self._client.close()
