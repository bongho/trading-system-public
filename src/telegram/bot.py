from __future__ import annotations

import logging

from telegram.ext import Application

from typing import TYPE_CHECKING

from src.agents.orchestrator import AgentOrchestrator
from src.brokers.base import BrokerAdapter
from src.config import settings
from src.db.repository import (
    PendingTradeRepository,
    StrategyRepository,
    TradeRepository,
)
from src.engine.executor import Executor
from src.strategies.registry import StrategyRegistry

if TYPE_CHECKING:
    from src.engine.hunting import HuntingLoop
    from src.engine.scheduler import TradingScheduler

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        brokers: dict[str, BrokerAdapter],
        registry: StrategyRegistry,
        executor: Executor,
        trade_repo: TradeRepository,
        strategy_repo: StrategyRepository,
        pending_repo: PendingTradeRepository,
        orchestrator: AgentOrchestrator | None = None,
    ) -> None:
        self.brokers = brokers
        self.registry = registry
        self.executor = executor
        self.trade_repo = trade_repo
        self.strategy_repo = strategy_repo
        self.pending_repo = pending_repo
        self.orchestrator = orchestrator
        self.scheduler: TradingScheduler | None = None
        self.hunter: HuntingLoop | None = None
        self.app: Application = (
            Application.builder().token(settings.telegram_bot_token).build()
        )

    def setup_handlers(self) -> None:
        from src.telegram.handlers.ai import register_ai_handlers
        from src.telegram.handlers.hg import register_hg_handlers
        from src.telegram.handlers.hunt import register_hunt_handlers
        from src.telegram.handlers.monitor import register_monitor_handlers
        from src.telegram.handlers.strategy import register_strategy_handlers
        from src.telegram.handlers.system import register_system_handlers
        from src.telegram.handlers.trade import register_trade_handlers

        from src.telegram.handlers.builder import register_builder_handlers

        register_system_handlers(self)
        register_trade_handlers(self)
        register_strategy_handlers(self)
        register_monitor_handlers(self)
        register_ai_handlers(self)
        register_hunt_handlers(self)
        register_hg_handlers(self)
        register_builder_handlers(self)
        logger.info("Telegram handlers registered")

    async def _register_commands(self) -> None:
        from telegram import BotCommand
        commands = [
            BotCommand("help",      "전체 명령어 목록"),
            BotCommand("status",    "전략 상태 + 잔고"),
            BotCommand("portfolio", "포트폴리오 조회"),
            BotCommand("hg",        "Holy Grail 포지션"),
            BotCommand("hg_scan",   "Holy Grail 즉시 스캔"),
            BotCommand("hg_bt",     "Holy Grail 백테스트"),
            BotCommand("dryrun",    "모의/실매매 전환"),
            BotCommand("skills",    "사용 가능한 전략 목록"),
            BotCommand("build",     "LLM으로 전략 생성"),
            BotCommand("bt",        "백테스트 실행"),
            BotCommand("sim",       "시뮬레이션 관리"),
            BotCommand("sims",      "시뮬레이션 목록"),
            BotCommand("stop",      "전략 긴급 정지"),
            BotCommand("history",   "최근 매매 이력"),
            BotCommand("signals",   "마지막 전략 시그널"),
            BotCommand("pnl",       "기간별 손익"),
            BotCommand("logs",      "최근 로그"),
        ]
        await self.app.bot.set_my_commands(commands)
        logger.info("Telegram command menu registered (%d commands)", len(commands))

    async def start(self) -> None:
        self.setup_handlers()
        await self.app.initialize()
        await self.app.start()
        await self._register_commands()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started")

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Telegram bot stopped")

    async def send_message(self, text: str) -> None:
        """알림 메시지 전송"""
        try:
            await self.app.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
            )
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
