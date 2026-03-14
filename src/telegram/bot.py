from __future__ import annotations

import logging

from telegram.ext import Application

from src.brokers.base import BrokerAdapter
from src.config import settings
from src.db.repository import (
    PendingTradeRepository,
    StrategyRepository,
    TradeRepository,
)
from src.engine.executor import Executor
from src.strategies.registry import StrategyRegistry

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
    ) -> None:
        self.brokers = brokers
        self.registry = registry
        self.executor = executor
        self.trade_repo = trade_repo
        self.strategy_repo = strategy_repo
        self.pending_repo = pending_repo
        self.app: Application = (
            Application.builder().token(settings.telegram_bot_token).build()
        )

    def setup_handlers(self) -> None:
        from src.telegram.handlers.strategy import register_strategy_handlers
        from src.telegram.handlers.system import register_system_handlers
        from src.telegram.handlers.trade import register_trade_handlers

        register_system_handlers(self)
        register_trade_handlers(self)
        register_strategy_handlers(self)
        logger.info("Telegram handlers registered")

    async def start(self) -> None:
        self.setup_handlers()
        await self.app.initialize()
        await self.app.start()
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
