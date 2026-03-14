from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import format_krw
from telegram import Update

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)


def register_trade_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    app = bot.app

    def _make(handler):
        @authorized_only
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await handler(update, context, bot)

        return wrapper

    app.add_handler(CommandHandler("trade", _make(_trade)))
    app.add_handler(CommandHandler("confirm", _make(_confirm)))
    app.add_handler(CommandHandler("cancel", _make(_cancel)))


@authorized_only
async def _trade(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """수동 매매: /trade buy upbit KRW-BTC 10000"""
    args = context.args or []
    if len(args) < 4:
        await update.message.reply_text(
            "사용법: /trade <buy|sell> <broker> <symbol> <amount>\n"
            "예시: /trade buy upbit KRW-BTC 10000"
        )
        return

    side = args[0].lower()
    broker = args[1].lower()
    symbol = args[2].upper()
    try:
        amount = float(args[3])
    except ValueError:
        await update.message.reply_text("❌ 금액은 숫자여야 합니다.")
        return

    if side not in ("buy", "sell"):
        await update.message.reply_text("❌ side는 buy 또는 sell이어야 합니다.")
        return

    if broker not in bot.brokers:
        await update.message.reply_text(f"❌ 알 수 없는 브로커: {broker}")
        return

    # 대기 주문 생성
    action = "매수" if side == "buy" else "매도"
    command = {
        "side": side,
        "broker": broker,
        "symbol": symbol,
        "amount": amount,
    }
    pending_id = await bot.pending_repo.create_pending(command)

    await update.message.reply_text(
        f"⏳ 주문 대기 중\n"
        f"  {action} {symbol} {format_krw(amount)} ({broker})\n"
        f"  ID: {pending_id}\n\n"
        f"/confirm {pending_id} - 실행\n"
        f"/cancel {pending_id} - 취소\n"
        f"(5분 후 자동 만료)"
    )


@authorized_only
async def _confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/confirm [pending_id]"""
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /confirm <pending_id>")
        return

    pending_id = args[0]
    pending = await bot.pending_repo.get_pending(pending_id)
    if not pending:
        await update.message.reply_text("❌ 대기 주문을 찾을 수 없거나 만료되었습니다.")
        return

    cmd = pending["command"]
    await update.message.reply_text("⏳ 주문 실행 중...")

    result = await bot.executor.execute_manual_trade(
        broker_name=cmd["broker"],
        side=cmd["side"],
        symbol=cmd["symbol"],
        amount=cmd["amount"],
    )

    await bot.pending_repo.delete_pending(pending_id)

    if result["success"]:
        tr = result["result"]
        await update.message.reply_text(
            f"✅ 체결 완료\n"
            f"  종목: {tr.symbol}\n"
            f"  금액: {format_krw(tr.amount)}\n"
            f"  가격: {format_krw(tr.price)}\n"
            f"  수량: {tr.volume:.8f}\n"
            f"  수수료: {format_krw(tr.fee)}"
        )
    else:
        await update.message.reply_text(f"❌ 주문 실패: {result['error']}")


@authorized_only
async def _cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/cancel [pending_id]"""
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /cancel <pending_id>")
        return

    pending_id = args[0]
    pending = await bot.pending_repo.get_pending(pending_id)
    if not pending:
        await update.message.reply_text(
            "❌ 대기 주문을 찾을 수 없거나 이미 만료되었습니다."
        )
        return

    await bot.pending_repo.delete_pending(pending_id)
    await update.message.reply_text(f"🗑️ 주문 취소됨: {pending_id}")
