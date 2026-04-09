from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from src.agents.orchestrator import AgentOrchestrator
from src.agents.sandbox import Sandbox
from src.brokers.base import BrokerAdapter, get_broker
from src.config import settings
from src.data.collector import MarketDataCollector
from src.db.repository import (
    MarketDataRepository,
    PendingTradeRepository,
    StrategyRepository,
    TradeRepository,
)
from src.db.schema import init_db
from src.engine.executor import Executor
from src.engine.risk_manager import RiskManager
from src.engine.scheduler import TradingScheduler
from src.reporters.discord import DiscordReporter
from src.reporters.telegram_notifier import TelegramNotifier
from src.strategies.double_bb_short import create_strategy as create_double_bb
from src.strategies.registry import StrategyRegistry
from src.strategies.simple_rsi import create_strategy as create_simple_rsi
from src.strategies.squeeze_mtf import create_strategy as create_squeeze_mtf
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
    market_repo = MarketDataRepository(db)

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

    if settings.kiwoom_app_key:
        brokers["kiwoom"] = get_broker(
            "kiwoom",
            app_key=settings.kiwoom_app_key,
            app_secret=settings.kiwoom_app_secret,
            access_token=settings.kiwoom_access_token,
            account_no=settings.kiwoom_account_no,
            is_paper=settings.kiwoom_is_paper,
        )
        logger.info("Kiwoom broker initialized (paper=%s)", settings.kiwoom_is_paper)
    else:
        logger.warning("Kiwoom credentials not configured")

    # 4. 전략 레지스트리
    registry = StrategyRegistry()

    # SimpleRSI 등록 (Phase 2)
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

    # DoubleBB Short 등록 (Phase 4)
    double_bb = create_double_bb(capital_allocation=100000)
    registry.register(double_bb)
    await strategy_repo.upsert_strategy(
        id=double_bb.id,
        name=double_bb.name,
        broker=double_bb.broker,
        symbols=double_bb.symbols,
        capital_allocation=double_bb.capital_allocation,
        interval_minutes=double_bb.interval_minutes,
        params=double_bb.params,
        code_path="src/strategies/double_bb_short.py",
    )

    # Squeeze MTF 등록 (Phase 4)
    squeeze_mtf = create_squeeze_mtf(capital_allocation=100000)
    registry.register(squeeze_mtf)
    await strategy_repo.upsert_strategy(
        id=squeeze_mtf.id,
        name=squeeze_mtf.name,
        broker=squeeze_mtf.broker,
        symbols=squeeze_mtf.symbols,
        capital_allocation=squeeze_mtf.capital_allocation,
        interval_minutes=squeeze_mtf.interval_minutes,
        params=squeeze_mtf.params,
        code_path="src/strategies/squeeze_mtf.py",
    )

    # 4b. 데이터 수집기 (Read-through 캐시)
    collector = MarketDataCollector(repo=market_repo, brokers=brokers)
    logger.info("Market data collector initialized")

    # 5. 리스크 매니저
    risk_manager = RiskManager(
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_position_pct=settings.max_position_pct,
    )

    # 6. AI Agent (Phase 5) — OpenAI 우선, Anthropic fallback
    orchestrator: AgentOrchestrator | None = None
    agent_backend = None
    if settings.openai_api_key:
        from src.agents.openai_backend import OpenAIBackend

        agent_backend = OpenAIBackend(
            api_key=settings.openai_api_key, model=settings.openai_model
        )
        logger.info("AI Agent backend: OpenAI (%s)", settings.openai_model)
    elif settings.anthropic_api_key:
        from src.agents.claude_backend import ClaudeDirectBackend

        agent_backend = ClaudeDirectBackend(api_key=settings.anthropic_api_key)
        logger.info("AI Agent backend: Claude Direct")

    swarm = None
    if agent_backend:
        sandbox = Sandbox()
        orchestrator = AgentOrchestrator(
            backend=agent_backend,
            registry=registry,
            trade_repo=trade_repo,
            brokers=brokers,
            sandbox=sandbox,
            collector=collector,
        )
        logger.info("AI Agent orchestrator initialized")

        # 6b. Swarm Consensus (Phase 6) — 신호 실행 전 3-에이전트 합의
        if settings.swarm_enabled:
            from src.agents.swarm import SwarmConsensus
            swarm = SwarmConsensus(
                backend=agent_backend,
                quorum=settings.swarm_quorum,
                min_signal_confidence=settings.swarm_min_confidence,
            )
            logger.info(
                "Swarm consensus enabled (quorum=%d/3, min_confidence=%.1f)",
                settings.swarm_quorum,
                settings.swarm_min_confidence,
            )
    else:
        orchestrator = None
        logger.warning("No AI API key configured — AI agent disabled")

    # 6b. Telegram Bot (알림 콜백으로 사용)
    bot = TradingBot(
        brokers=brokers,
        registry=registry,
        executor=None,  # 아래에서 설정
        trade_repo=trade_repo,
        strategy_repo=strategy_repo,
        pending_repo=pending_repo,
        orchestrator=orchestrator,
    )

    # 7. Notifier
    notifier = TelegramNotifier(bot.send_message)

    # 7b. Discord Reporter
    discord_reporter: DiscordReporter | None = None
    if settings.discord_webhook_url:
        discord_reporter = DiscordReporter(settings.discord_webhook_url, trade_repo)
        logger.info("Discord reporter initialized")

    # 8. Executor (with write-through market data caching)
    executor = Executor(
        brokers=brokers,
        risk_manager=risk_manager,
        trade_repo=trade_repo,
        notify_callback=notifier.notify_trade,
        market_data_repo=market_repo,
        swarm=swarm,
    )
    bot.executor = executor

    # 9. Scheduler (with daily Discord report)
    scheduler = TradingScheduler(
        registry=registry,
        executor=executor,
        daily_report_callback=(
            discord_reporter.send_daily_report if discord_reporter else None
        ),
    )

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
