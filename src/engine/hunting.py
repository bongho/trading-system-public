"""Hunting Mode — 목표 수익률 기반 자율 전략 관리.

두 가지 모드:
- watch: 기존 전략 모니터링 + 미달 시 Telegram 알림
- hunt:  예산 할당 + 미달 전략 자동 교체 (optimize → replace)
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from typing import TYPE_CHECKING

from src.db.repository import StrategyRepository, TradeRepository
from src.strategies.registry import StrategyRegistry

if TYPE_CHECKING:
    from src.engine.scheduler import TradingScheduler

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 미달 판단 기준
UNDERPERFORM_PNL_THRESHOLD = -0.02  # -2% 손실
UNDERPERFORM_MIN_TRADES = 10
UNDERPERFORM_WIN_RATE = 35.0


@dataclass
class HuntingSession:
    mode: str                           # "watch" | "hunt"
    target_return: float                # 목표 수익률 (0.10 = 10%)
    max_drawdown: float                 # 최대 허용 낙폭 (0.05 = 5%)
    budget: float                       # hunt 전략당 기본 자본 (KRW)
    eval_interval_hours: int            # 평가 주기 (기본 1시간)
    started_at: datetime = field(default_factory=lambda: datetime.now(KST))
    status: str = "running"             # running | stopped | target_reached | drawdown_exceeded
    strategy_ids: list[str] = field(default_factory=list)  # hunt 모드 소유 전략 ID
    replaced_count: int = 0


class HuntingLoop:
    """목표 수익률 기반 전략 자동 관리 루프.

    기존 컴포넌트 조합:
    - orchestrator.optimize()           — 미달 전략 자동 최적화
    - orchestrator.create_new_strategy() — 신규 전략 생성
    - orchestrator.confirm_new_strategy() — sandbox → strategies/ 자동 적용
    - scheduler.add_strategy()          — 스케줄 등록
    - registry.register()               — 런타임 등록
    """

    def __init__(
        self,
        session: HuntingSession,
        orchestrator: Any,
        scheduler: Any,  # TradingScheduler (TYPE_CHECKING only)
        registry: StrategyRegistry,
        trade_repo: TradeRepository,
        strategy_repo: StrategyRepository,
        notify_callback: Callable[[str], Coroutine],
    ) -> None:
        self._session = session
        self._orchestrator = orchestrator
        self._scheduler = scheduler
        self._registry = registry
        self._trade_repo = trade_repo
        self._strategy_repo = strategy_repo
        self._notify = notify_callback
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """평가 루프 비동기 태스크 시작."""
        self._session.status = "running"
        self._task = asyncio.create_task(self._loop(), name="hunting_loop")
        logger.info(
            "HuntingLoop started: mode=%s target=%.1f%% max_dd=%.1f%%",
            self._session.mode,
            self._session.target_return * 100,
            self._session.max_drawdown * 100,
        )

    async def stop(self) -> None:
        """루프 중지."""
        self._session.status = "stopped"
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HuntingLoop stopped")

    @property
    def session(self) -> HuntingSession:
        return self._session

    def get_status(self) -> dict[str, Any]:
        elapsed = datetime.now(KST) - self._session.started_at
        hours, rem = divmod(int(elapsed.total_seconds()), 3600)
        minutes = rem // 60
        return {
            "mode": self._session.mode,
            "status": self._session.status,
            "target_return": self._session.target_return,
            "max_drawdown": self._session.max_drawdown,
            "budget": self._session.budget,
            "strategy_ids": list(self._session.strategy_ids),
            "replaced_count": self._session.replaced_count,
            "elapsed": f"{hours}h {minutes}m",
            "eval_interval_hours": self._session.eval_interval_hours,
        }

    # ------------------------------------------------------------------
    # 루프 내부
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """평가 주기 루프 (asyncio.sleep 기반)."""
        # hunt 모드: 초기 전략 1개 즉시 생성
        if self._session.mode == "hunt" and not self._session.strategy_ids:
            await self._create_initial_strategy()

        while self._session.status == "running":
            await asyncio.sleep(self._session.eval_interval_hours * 3600)
            if self._session.status != "running":
                break
            try:
                await self._evaluate()
            except Exception as e:
                logger.error("Hunting evaluation error: %s", e, exc_info=True)
                await self._notify(f"⚠️ 사냥 루프 오류: {e}")

    async def _evaluate(self) -> None:
        """전략 성과 평가 → 목표 체크 → 미달 처리."""
        # 1. 대상 전략 수집
        if self._session.mode == "hunt":
            strategy_ids = list(self._session.strategy_ids)
        else:
            strategy_ids = [s.id for s in self._registry.get_enabled()]

        if not strategy_ids:
            await self._notify("ℹ️ [Hunting] 평가 대상 전략이 없습니다.")
            return

        # 2. 전략별 DB 데이터 + 성과 수집
        performances: list[dict[str, Any]] = []
        for sid in strategy_ids:
            db_data = await self._strategy_repo.get_strategy(sid)
            if not db_data:
                continue
            capital_alloc = float(db_data.get("capital_allocation", 1))
            current_cap = float(db_data.get("current_capital", capital_alloc))
            pnl_pct = (current_cap - capital_alloc) / capital_alloc if capital_alloc else 0

            stats = await self._trade_repo.get_strategy_stats(sid)
            total_trades = int(stats.get("total_trades") or 0)
            win_count = int(stats.get("win_count") or 0)
            win_rate = (win_count / total_trades * 100) if total_trades else 0.0

            performances.append({
                "id": sid,
                "name": db_data.get("name", sid),
                "capital_allocation": capital_alloc,
                "current_capital": current_cap,
                "pnl_pct": pnl_pct,
                "total_trades": total_trades,
                "win_rate": win_rate,
            })

        if not performances:
            return

        # 3. 포트폴리오 합산 수익률
        total_alloc = sum(p["capital_allocation"] for p in performances)
        total_current = sum(p["current_capital"] for p in performances)
        portfolio_return = (total_current - total_alloc) / total_alloc if total_alloc else 0

        logger.info(
            "Hunting eval: portfolio_return=%.2f%% strategies=%d",
            portfolio_return * 100, len(performances),
        )

        # 4. 목표 달성 확인
        if portfolio_return >= self._session.target_return:
            self._session.status = "target_reached"
            msg = (
                f"🎯 목표 달성!\n"
                f"  포트폴리오 수익률: {portfolio_return:.1%}\n"
                f"  목표: {self._session.target_return:.1%}\n"
                f"  총 전략 교체: {self._session.replaced_count}회\n"
                f"  자동사냥 중지됨"
            )
            await self._notify(msg)
            return

        # 5. 낙폭 초과 확인
        if portfolio_return <= -self._session.max_drawdown:
            self._session.status = "drawdown_exceeded"
            # hunt 모드: 전략 모두 pause
            if self._session.mode == "hunt":
                for sid in self._session.strategy_ids:
                    self._registry.set_enabled(sid, False)
                    await self._strategy_repo.set_enabled(sid, False)
            msg = (
                f"🛑 최대 낙폭 초과!\n"
                f"  포트폴리오 수익률: {portfolio_return:.1%}\n"
                f"  허용 낙폭: -{self._session.max_drawdown:.1%}\n"
                f"  자동사냥 중지됨"
            )
            await self._notify(msg)
            return

        # 6. 미달 전략 처리
        underperformers = [
            p for p in performances
            if self._is_underperforming(p)
        ]

        if not underperformers:
            return  # 모두 정상

        if self._session.mode == "watch":
            await self._notify_underperformers(underperformers, portfolio_return)
        else:
            await self._replace_underperformers(underperformers)

    def _is_underperforming(self, perf: dict[str, Any]) -> bool:
        """미달 여부 판단."""
        pnl_below = perf["pnl_pct"] < UNDERPERFORM_PNL_THRESHOLD
        trade_below = (
            perf["total_trades"] >= UNDERPERFORM_MIN_TRADES
            and perf["win_rate"] < UNDERPERFORM_WIN_RATE
        )
        return pnl_below or trade_below

    async def _notify_underperformers(
        self, underperformers: list[dict], portfolio_return: float
    ) -> None:
        """watch 모드: 미달 전략 알림."""
        lines = [
            f"⚠️ [Watch] 미달 전략 감지 ({len(underperformers)}개)",
            f"포트폴리오: {portfolio_return:.1%} | 목표: {self._session.target_return:.1%}",
            "",
        ]
        for p in underperformers:
            lines.append(
                f"  • {p['name']}: {p['pnl_pct']:.1%} "
                f"(승률 {p['win_rate']:.0f}%, {p['total_trades']}건)"
            )
        await self._notify("\n".join(lines))

    async def _replace_underperformers(
        self, underperformers: list[dict]
    ) -> None:
        """hunt 모드: 미달 전략 최적화 → 실패 시 교체."""
        for perf in underperformers:
            strategy_id = perf["id"]
            strategy = self._registry.get(strategy_id)
            if not strategy:
                continue

            await self._notify(
                f"🔄 미달 전략 최적화 시도: {perf['name']} ({perf['pnl_pct']:.1%})"
            )

            # 1차 시도: optimize
            opt_result = await self._orchestrator.optimize(strategy_id)
            if opt_result.success and opt_result.review and opt_result.review.approved:
                applied = await self._orchestrator.confirm_proposal(opt_result.session_id)
                if applied:
                    await self._notify(f"✅ 전략 최적화 완료: {perf['name']}")
                    continue

            # 최적화 실패 → 전략 교체
            await self._notify(f"♻️ 전략 교체 중: {perf['name']} → 신규 전략 생성")

            # 기존 전략 비활성화
            self._registry.set_enabled(strategy_id, False)
            await self._strategy_repo.set_enabled(strategy_id, False)
            if strategy_id in self._session.strategy_ids:
                self._session.strategy_ids.remove(strategy_id)

            # 신규 전략 생성 + 자동 confirm
            description = (
                f"업비트 BTC/ETH 복합 전략 (RSI + 볼린저밴드), "
                f"초기 자본 {int(self._session.budget):,}원"
            )
            try:
                session_id, meta = await self._orchestrator.create_new_strategy(
                    description, capital_allocation=self._session.budget
                )
                new_meta = await self._orchestrator.confirm_new_strategy(session_id)
                if new_meta:
                    await self._register_new_strategy(new_meta)
                    self._session.replaced_count += 1
                    await self._notify(
                        f"🆕 신규 전략 등록: {new_meta['strategy_name']} "
                        f"(교체 {self._session.replaced_count}회)"
                    )
            except Exception as e:
                logger.error("Strategy replacement failed: %s", e, exc_info=True)
                await self._notify(f"❌ 전략 교체 실패: {e}")

    async def _create_initial_strategy(self) -> None:
        """hunt 모드 시작 시 초기 전략 1개 생성."""
        await self._notify("🤖 [Hunt] 초기 전략 생성 중...")
        description = (
            f"업비트 BTC RSI + 볼린저밴드 복합 전략, "
            f"초기 자본 {int(self._session.budget):,}원"
        )
        try:
            session_id, meta = await self._orchestrator.create_new_strategy(
                description, capital_allocation=self._session.budget
            )
            new_meta = await self._orchestrator.confirm_new_strategy(session_id)
            if new_meta:
                await self._register_new_strategy(new_meta)
                bt = meta["backtest"]
                await self._notify(
                    f"✅ 초기 전략 등록: {meta['strategy_name']}\n"
                    f"  백테스트: {bt['total_pnl_pct']:.1f}% | "
                    f"승률 {bt['win_rate']:.1f}% | "
                    f"샤프 {bt['sharpe_ratio']:.2f}"
                )
        except Exception as e:
            logger.error("Initial strategy creation failed: %s", e, exc_info=True)
            await self._notify(f"❌ 초기 전략 생성 실패: {e}\n평가는 계속 진행합니다.")

    async def _register_new_strategy(self, meta: dict[str, Any]) -> None:
        """신규 전략 registry + DB + scheduler 등록."""
        strategy_id = meta["strategy_id"]

        importlib.invalidate_caches()
        try:
            mod = importlib.import_module(f"src.strategies.{strategy_id}")
        except ImportError:
            path = Path(f"src/strategies/{strategy_id}.py")
            spec = importlib.util.spec_from_file_location(strategy_id, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

        strategy = mod.create_strategy(capital_allocation=meta["capital_allocation"])
        self._registry.register(strategy)

        await self._strategy_repo.upsert_strategy(
            id=strategy_id,
            name=meta["strategy_name"],
            broker=meta["broker"],
            symbols=meta["symbols"],
            capital_allocation=meta["capital_allocation"],
            interval_minutes=meta["interval_minutes"],
            enabled=True,
            code_path=f"src/strategies/{strategy_id}.py",
        )

        self._scheduler.add_strategy(strategy_id, meta["interval_minutes"])
        self._session.strategy_ids.append(strategy_id)
