from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import format_strategy_status
from telegram import Update

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)


def register_strategy_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    app = bot.app

    def _make(handler):
        @authorized_only
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await handler(update, context, bot)

        return wrapper

    app.add_handler(CommandHandler("strategy", _make(_strategy)))
    app.add_handler(CommandHandler("param", _make(_param)))
    app.add_handler(CommandHandler("backtest", _make(_backtest)))


@authorized_only
async def _strategy(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/strategy list|pause|resume <id>"""
    args = context.args or []
    if not args:
        args = ["list"]

    action = args[0].lower()

    if action == "list":
        strategies = bot.registry.get_all()
        data = []
        for s in strategies:
            db_data = await bot.strategy_repo.get_strategy(s.id)
            data.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "broker": s.broker,
                    "enabled": s.enabled,
                    "interval_minutes": s.interval_minutes,
                    "capital_allocation": s.capital_allocation,
                    "current_capital": db_data["current_capital"]
                    if db_data
                    else s.capital_allocation,
                }
            )
        await update.message.reply_text(format_strategy_status(data))

    elif action == "pause" and len(args) > 1:
        strategy_id = args[1]
        if bot.registry.set_enabled(strategy_id, False):
            await bot.strategy_repo.set_enabled(strategy_id, False)
            await update.message.reply_text(f"⏸️ 전략 일시 정지: {strategy_id}")
        else:
            await update.message.reply_text(f"❌ 전략을 찾을 수 없음: {strategy_id}")

    elif action == "resume" and len(args) > 1:
        strategy_id = args[1]
        if bot.registry.set_enabled(strategy_id, True):
            await bot.strategy_repo.set_enabled(strategy_id, True)
            await update.message.reply_text(f"▶️ 전략 재개: {strategy_id}")
        else:
            await update.message.reply_text(f"❌ 전략을 찾을 수 없음: {strategy_id}")

    else:
        await update.message.reply_text(
            "사용법:\n"
            "/strategy list - 목록\n"
            "/strategy pause <id> - 일시 정지\n"
            "/strategy resume <id> - 재개"
        )


@authorized_only
async def _param(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/param <strategy_id> <key> <value>"""
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "사용법: /param <strategy_id> <key> <value>\n"
            "예시: /param simple_rsi rsi_period 21"
        )
        return

    strategy_id = args[0]
    key = args[1]
    raw_value = args[2]

    # 값 타입 추론
    try:
        value: int | float | str | bool = json.loads(raw_value)
    except (json.JSONDecodeError, ValueError):
        value = raw_value

    strategy = bot.registry.get(strategy_id)
    if not strategy:
        await update.message.reply_text(f"❌ 전략을 찾을 수 없음: {strategy_id}")
        return

    old_value = strategy.params.get(key, "N/A")
    strategy.params[key] = value

    # DB 동기화
    await bot.strategy_repo.update_params(strategy_id, strategy.params)

    await update.message.reply_text(
        f"⚙️ 파라미터 변경\n  전략: {strategy_id}\n  {key}: {old_value} → {value}"
    )


@authorized_only
async def _backtest(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/backtest <strategy_id> [days]"""
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /backtest <strategy_id> [days]")
        return

    await update.message.reply_text("🔄 백테스트는 Phase 2에서 구현 예정입니다.")
