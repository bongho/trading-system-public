from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from src.brokers.base import BrokerAdapter, get_broker
from src.config import settings
from src.db.repository import (
    PendingTradeRepository,
    StrategyRepository,
    TradeRepository,
)
from src.db.schema import init_db
from src.engine.executor import Executor
from src.engine.risk_manager import RiskManager
from src.engine.scheduler import TradingScheduler
from src.reporters.telegram_notifier import TelegramNotifier
from src.strategies.registry import StrategyRegistry
from src.strategies.simple_rsi import create_strategy as create_simple_rsi
from src.telegram.bot import TradingBot

os.makedirs("data/logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/logs/trading.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("=== Trading System Starting ===")

    # 1. 데이터 디렉토리 확보
    os.makedirs("data/logs", exist_ok=True)
    os.makedirs("data/backups", exist_ok=True)

    # 2. DB 초기화
    db = await init_db(settings.db_path)
    trade_repo = TradeRepository(db)
    strategy_repo = StrategyRepository(db)
    pending_repo = PendingTradeRepository(db)

    # 3. 브로커 초기화
    brokers: dict[str, BrokerAdapter] = {}
    if settings.upbit_access_key:
        brokers["upbit"] = get_broker(
            "upbit",
            access_key=settings.upbit_access_key,
            secret_key=settings.upbit_secret_key,
        )
        logger.info("Upbit broker initialized")
    else:
        logger.warning("Upbit credentials not configured")

    # 4. 전략 레지스트리
    registry = StrategyRegistry()

    # SimpleRSI 등록 (Phase 2 검증용)
    simple_rsi = create_simple_rsi(capital_allocation=100000)
    registry.register(simple_rsi)
    await strategy_repo.upsert_strategy(
        id=simple_rsi.id,
        name=simple_rsi.name,
        broker=simple_rsi.broker,
        symbols=simple_rsi.symbols,
        capital_allocation=simple_rsi.capital_allocation,
        interval_minutes=simple_rsi.interval_minutes,
        params=simple_rsi.params,
        code_path="src/strategies/simple_rsi.py",
    )

    # 5. 리스크 매니저
    risk_manager = RiskManager(
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_position_pct=settings.max_position_pct,
    )

    # 6. Telegram Bot (알림 콜백으로 사용)
    bot = TradingBot(
        brokers=brokers,
        registry=registry,
        executor=None,  # 아래에서 설정
        trade_repo=trade_repo,
        strategy_repo=strategy_repo,
        pending_repo=pending_repo,
    )

    # 7. Notifier
    notifier = TelegramNotifier(bot.send_message)

    # 8. Executor
    executor = Executor(
        brokers=brokers,
        risk_manager=risk_manager,
        trade_repo=trade_repo,
        notify_callback=notifier.notify_trade,
    )
    bot.executor = executor

    # 9. Scheduler
    scheduler = TradingScheduler(registry=registry, executor=executor)

    # 10. 시작
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        # Telegram Bot 시작
        if settings.telegram_bot_token:
            await bot.start()
            await bot.send_message("🚀 자동매매 시스템이 시작되었습니다.")
        else:
            logger.warning("Telegram bot token not configured")

        # 스케줄러 시작
        scheduler.start()
        logger.info("=== Trading System Ready ===")

        # 종료 시그널 대기
        await shutdown_event.wait()

    finally:
        logger.info("=== Trading System Shutting Down ===")
        scheduler.stop()

        if settings.telegram_bot_token:
            await bot.send_message("🛑 자동매매 시스템이 종료됩니다.")
            await bot.stop()

        for broker in brokers.values():
            await broker.close()

        await db.close()
        logger.info("=== Trading System Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
