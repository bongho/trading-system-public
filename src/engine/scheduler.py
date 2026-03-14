from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.engine.executor import Executor
from src.strategies.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class TradingScheduler:
    def __init__(
        self,
        registry: StrategyRegistry,
        executor: Executor,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self._jobs: dict[str, str] = {}  # strategy_id -> job_id

    def start(self) -> None:
        # 각 활성 전략에 대해 개별 스케줄 등록
        for strategy in self._registry.get_all():
            self._add_strategy_job(strategy.id, strategy.interval_minutes)

        # 일일 리셋 (00:00 KST)
        self._scheduler.add_job(
            self._daily_reset,
            "cron",
            hour=0,
            minute=0,
            id="daily_reset",
        )

        self._scheduler.start()
        logger.info("Trading scheduler started with %d strategies", len(self._jobs))

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Trading scheduler stopped")

    def _add_strategy_job(self, strategy_id: str, interval_minutes: int) -> None:
        job_id = f"strategy_{strategy_id}"
        self._scheduler.add_job(
            self._run_strategy,
            "interval",
            minutes=interval_minutes,
            id=job_id,
            args=[strategy_id],
            replace_existing=True,
        )
        self._jobs[strategy_id] = job_id
        logger.info(
            "Scheduled strategy %s every %d minutes", strategy_id, interval_minutes
        )

    def add_strategy(self, strategy_id: str, interval_minutes: int) -> None:
        self._add_strategy_job(strategy_id, interval_minutes)

    def remove_strategy(self, strategy_id: str) -> None:
        job_id = self._jobs.pop(strategy_id, None)
        if job_id:
            self._scheduler.remove_job(job_id)
            logger.info("Removed strategy schedule: %s", strategy_id)

    async def _run_strategy(self, strategy_id: str) -> None:
        strategy = self._registry.get(strategy_id)
        if not strategy or not strategy.enabled:
            return

        logger.debug("Running strategy: %s", strategy_id)
        try:
            await self._executor.run_strategy(strategy)
        except Exception as e:
            logger.error("Strategy %s run failed: %s", strategy_id, e, exc_info=True)

    async def _daily_reset(self) -> None:
        logger.info("Daily reset triggered")
        self._executor._risk.reset_daily()

    async def run_once(self, strategy_id: str) -> list:
        """수동으로 전략 1회 실행"""
        strategy = self._registry.get(strategy_id)
        if not strategy:
            return []
        return await self._executor.run_strategy(strategy)
