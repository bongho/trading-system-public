from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import (
    format_krw,
    format_pct,
    format_pnl,
    format_strategy_status,
)
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
    """/backtest <strategy_id> [days] - 히스토리컬 데이터 백테스트"""
    args = context.args or []
    if not args:
        ids = [s.id for s in bot.registry.get_all()]
        await update.message.reply_text(
            "사용법: /backtest <strategy_id> [days]\n"
            f"예시: /backtest simple_rsi 30\n\n"
            f"등록된 전략: {', '.join(ids) or '없음'}"
        )
        return

    strategy_id = args[0]
    days = 30
    if len(args) > 1:
        try:
            days = int(args[1])
            if days < 1 or days > 365:
                await update.message.reply_text("❌ days는 1~365 범위로 입력하세요.")
                return
        except ValueError:
            await update.message.reply_text("❌ days는 숫자로 입력하세요.")
            return

    strategy = bot.registry.get(strategy_id)
    if not strategy:
        await update.message.reply_text(f"❌ 전략을 찾을 수 없음: {strategy_id}")
        return

    broker = bot.brokers.get(strategy.broker)
    if not broker:
        await update.message.reply_text(f"❌ 브로커 미연결: {strategy.broker}")
        return

    await update.message.reply_text(
        f"⏳ 백테스트 실행 중...\n"
        f"  전략: {strategy.name}\n"
        f"  기간: {days}일\n"
        f"  심볼: {', '.join(strategy.symbols)}\n"
        f"  (데이터 수집에 시간이 걸릴 수 있습니다)"
    )

    try:
        # 히스토리컬 데이터 수집
        historical: dict = {}
        for symbol in strategy.symbols:
            data = await broker.get_historical_data(symbol, "5m", days)
            if data:
                historical[symbol] = data

        if not historical:
            await update.message.reply_text(
                "❌ 히스토리컬 데이터를 가져올 수 없습니다."
            )
            return

        # 백테스트 실행
        result = await strategy.backtest(historical)

        # 결과 포맷
        total_candles = sum(len(v) for v in historical.values())
        pnl_emoji = "📈" if result.total_pnl >= 0 else "📉"

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 백테스트 결과: {strategy.name}",
            f"  기간: {days}일 | 캔들: {total_candles:,}개",
            f"  초기 자본: {format_krw(strategy.capital_allocation)}",
            "",
            f"{pnl_emoji} 성과",
            f"  총 손익: {format_pnl(result.total_pnl)} ({format_pct(result.total_pnl_pct)})",
            f"  총 매매: {result.total_trades}건",
            f"  승률: {result.win_rate:.1f}% ({result.win_count}승 {result.loss_count}패)",
            "",
            "📐 리스크 지표",
            f"  최대 낙폭 (MDD): {format_pct(result.max_drawdown)}",
            f"  샤프 비율: {result.sharpe_ratio:.2f}",
            f"  평균 이익: {format_pnl(result.avg_profit)}",
            f"  평균 손실: {format_krw(result.avg_loss)}",
        ]

        # 최근 거래 내역
        if result.trades:
            lines.append("")
            lines.append("🔄 최근 거래 (최대 10건)")
            for t in result.trades:
                emoji = "✅" if t["pnl"] > 0 else "❌"
                lines.append(
                    f"  {emoji} {t['symbol']} {format_pnl(t['pnl'])} "
                    f"({format_pct(t['pnl_pct'] * 100)})"
                )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        logger.error("Backtest failed: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ 백테스트 실패: {e}")
