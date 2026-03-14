from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import format_portfolio_text, format_strategy_status
from telegram import Update

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)


def register_system_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    app = bot.app
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("status", _make_handler(bot, _status)))
    app.add_handler(CommandHandler("portfolio", _make_handler(bot, _portfolio)))
    app.add_handler(CommandHandler("history", _make_handler(bot, _history)))
    app.add_handler(CommandHandler("logs", _make_handler(bot, _logs)))


def _make_handler(bot: TradingBot, handler):
    """bot 인스턴스를 handler에 주입"""

    @authorized_only
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await handler(update, context, bot)

    return wrapper


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 자동매매 시스템 봇입니다.\n/help 로 명령어를 확인하세요."
    )


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = """📖 명령어 목록

📊 조회
/status - 전체 상태
/portfolio [broker] - 포트폴리오
/history [N] - 최근 N건 매매 이력

💰 매매
/trade buy upbit KRW-BTC 10000 - 수동 매수
/confirm - 대기 주문 확인
/cancel - 대기 주문 취소

⚙️ 전략
/strategy list - 전략 목록
/strategy pause <id> - 일시 정지
/strategy resume <id> - 재개
/param <strategy> <key> <value> - 파라미터 변경

🔧 시스템
/logs [N] - 최근 로그
/restart - 시스템 재시작"""
    await update.message.reply_text(text)


@authorized_only
async def _status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    strategies = bot.registry.get_all()
    strategy_data = []
    for s in strategies:
        db_data = await bot.strategy_repo.get_strategy(s.id)
        strategy_data.append(
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

    text = format_strategy_status(strategy_data)

    # 각 브로커 잔고 요약
    for broker_name, broker in bot.brokers.items():
        try:
            portfolio = await broker.get_portfolio()
            text += f"\n\n💰 {broker_name.upper()}: {portfolio.total_value:,.0f} KRW"
        except Exception as e:
            text += f"\n\n❌ {broker_name.upper()}: 연결 실패 ({e})"

    await update.message.reply_text(text)


@authorized_only
async def _portfolio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    args = context.args or []
    broker_filter = args[0] if args else None

    for broker_name, broker in bot.brokers.items():
        if broker_filter and broker_name != broker_filter:
            continue
        try:
            portfolio = await broker.get_portfolio()
            data = {
                "broker": broker_name,
                "total_value": portfolio.total_value,
                "available_balance": portfolio.available_balance,
                "items": [
                    {
                        "symbol": item.symbol,
                        "value": item.value,
                        "pnl_pct": item.pnl_pct,
                    }
                    for item in portfolio.items
                ],
            }
            await update.message.reply_text(format_portfolio_text(data))
        except Exception as e:
            await update.message.reply_text(f"❌ {broker_name}: {e}")


@authorized_only
async def _history(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    args = context.args or []
    limit = int(args[0]) if args else 10

    trades = await bot.trade_repo.get_recent_trades(limit=limit)
    if not trades:
        await update.message.reply_text("매매 이력이 없습니다.")
        return

    lines = [f"📜 최근 {len(trades)}건 매매 이력", "━━━━━━━━━━━━━━━━━━━━"]
    for t in trades:
        emoji = "🟢" if t["side"] == "buy" else "🔴"
        pnl_str = ""
        if t["pnl"] is not None:
            pnl_str = f" | P&L: ₩{t['pnl']:+,.0f}"
        lines.append(
            f"{emoji} [{t['executed_at'][:16]}] {t['side'].upper()} "
            f"{t['symbol']} ₩{t['amount']:,.0f}{pnl_str}"
        )

    await update.message.reply_text("\n".join(lines))


@authorized_only
async def _logs(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    await update.message.reply_text("📋 로그 조회는 추후 구현 예정입니다.")
