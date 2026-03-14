"""모니터링/분석 명령어: /report, /pnl, /price, /stop, /signals"""

from __future__ import annotations

import logging
import os
import time
from datetime import timedelta, timezone
from typing import TYPE_CHECKING

from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import format_krw, format_pct, format_pnl
from telegram import Update

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
_START_TIME = time.monotonic()


def register_monitor_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    app = bot.app

    def _make(handler):
        @authorized_only
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await handler(update, context, bot)

        return wrapper

    app.add_handler(CommandHandler("report", _make(_report)))
    app.add_handler(CommandHandler("pnl", _make(_pnl)))
    app.add_handler(CommandHandler("price", _make(_price)))
    app.add_handler(CommandHandler("stop", _make(_stop)))
    app.add_handler(CommandHandler("signals", _make(_signals)))
    app.add_handler(CommandHandler("risk", _make(_risk)))
    app.add_handler(CommandHandler("health", _make(_health)))


@authorized_only
async def _report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/report - 즉시 일간 리포트 생성"""
    strategies = bot.registry.get_all()
    if not strategies:
        await update.message.reply_text("등록된 전략이 없습니다.")
        return

    for strategy in strategies:
        stats = await bot.trade_repo.get_strategy_stats(strategy.id)
        recent = await bot.trade_repo.get_recent_trades(strategy.id, limit=5)

        capital = strategy.capital_allocation
        total_pnl = stats.get("total_pnl", 0) or 0
        current = capital + total_pnl
        pnl_pct = (total_pnl / capital * 100) if capital else 0

        win = stats.get("win_count", 0) or 0
        loss = stats.get("loss_count", 0) or 0
        total = stats.get("total_trades", 0) or 0
        win_rate = (win / total * 100) if total > 0 else 0

        avg_profit = stats.get("avg_profit", 0) or 0
        avg_loss = stats.get("avg_loss", 0) or 0
        rr = abs(avg_profit / avg_loss) if avg_loss else 0
        rr_label = "✅ 양호" if rr >= 1.0 else "⚠️ 개선필요"

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        status = "🟢" if strategy.enabled else "⏸️"

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"〈 {strategy.name} 〉 {status}",
            "",
            "📅 현재 현황",
            f"  현재 캐파: {format_krw(current)} "
            f"({pnl_emoji} {format_pct(pnl_pct)} from {format_krw(capital)})",
            "",
            "📊 누적 성과",
            f"  손익: {pnl_emoji} {format_pnl(total_pnl)} ({format_pct(pnl_pct)})",
            f"  승률: {win_rate:.0f}%  ({win}승 {loss}패 / 총 {total}건)",
            "",
            "📐 손익비 분석",
            f"  평균 이익: {format_pnl(avg_profit)}",
            f"  평균 손실: {format_krw(avg_loss)}",
            f"  손익비 (RR): {rr:.2f} {rr_label}",
            f"  최대 이익: {format_pnl(stats.get('max_profit', 0) or 0)}",
            f"  최대 손실: {format_krw(stats.get('max_loss', 0) or 0)}",
        ]

        # 최근 청산
        sell_trades = [
            t for t in recent if t["side"] == "sell" and t["pnl"] is not None
        ]
        if sell_trades:
            lines.append("")
            lines.append("🔄 최근 청산")
            for t in sell_trades:
                emoji = "✅" if t["pnl"] > 0 else "❌"
                action = "익절" if t["pnl"] > 0 else "손절"
                ts = t["executed_at"][:16].replace("T", " ")
                lines.append(
                    f"  {emoji} [{ts}] {action} {t['symbol']}  {format_pnl(t['pnl'])}"
                )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await update.message.reply_text("\n".join(lines))


@authorized_only
async def _pnl(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/pnl [today|week|month] - 기간별 손익"""
    args = context.args or []
    period = args[0].lower() if args else "today"
    if period not in ("today", "week", "month", "all"):
        await update.message.reply_text("사용법: /pnl [today|week|month|all]")
        return

    result = await bot.trade_repo.get_pnl_by_period(period)
    period_label = {
        "today": "오늘",
        "week": "최근 7일",
        "month": "최근 30일",
        "all": "전체",
    }[period]

    strategies = result["strategies"]
    if not strategies:
        await update.message.reply_text(f"📊 {period_label} 손익: 해당 기간 매매 없음")
        return

    total_pnl = sum(s["total_pnl"] for s in strategies)
    total_trades = sum(s["trades"] for s in strategies)
    total_wins = sum(s["wins"] for s in strategies)
    total_fee = sum(s["total_fee"] for s in strategies)
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"

    lines = [
        f"{pnl_emoji} {period_label} 손익 ({result['since']}~)",
        "━━━━━━━━━━━━━━━━━━━━",
        f"총 손익: {format_pnl(total_pnl)}",
        f"총 수수료: {format_krw(total_fee)}",
        f"순 손익: {format_pnl(total_pnl - total_fee)}",
        f"매매: {total_trades}건 (승 {total_wins})",
        "",
    ]

    for s in strategies:
        emoji = "📈" if s["total_pnl"] >= 0 else "📉"
        lines.append(
            f"  {emoji} {s['strategy_id']}: "
            f"{format_pnl(s['total_pnl'])} "
            f"({s['trades']}건, 승 {s['wins']})"
        )

    await update.message.reply_text("\n".join(lines))


@authorized_only
async def _price(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/price KRW-BTC - 현재가 조회"""
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /price <symbol>\n예시: /price KRW-BTC")
        return

    symbol = args[0].upper()
    broker = bot.brokers.get("upbit")
    if not broker:
        await update.message.reply_text("❌ 업비트 브로커 미연결")
        return

    try:
        price = await broker.get_current_price(symbol)
        # 24h 변동 정보를 위해 market data 조회
        data = await broker.get_market_data(symbol, "1d", 2)
        if len(data) >= 2:
            prev_close = data[-2].close
            change = price - prev_close
            change_pct = (change / prev_close) * 100
            emoji = "📈" if change >= 0 else "📉"
            await update.message.reply_text(
                f"💰 {symbol}\n"
                f"  현재가: {format_krw(price)}\n"
                f"  24h: {emoji} {format_pnl(change)} ({format_pct(change_pct)})\n"
                f"  고가: {format_krw(data[-1].high)}\n"
                f"  저가: {format_krw(data[-1].low)}"
            )
        else:
            await update.message.reply_text(f"💰 {symbol}: {format_krw(price)}")
    except Exception as e:
        await update.message.reply_text(f"❌ 조회 실패: {e}")


@authorized_only
async def _stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/stop - 전체 전략 긴급 정지"""
    strategies = bot.registry.get_all()
    stopped = 0
    for s in strategies:
        if s.enabled:
            bot.registry.set_enabled(s.id, False)
            await bot.strategy_repo.set_enabled(s.id, False)
            stopped += 1

    await update.message.reply_text(
        f"🛑 전체 전략 긴급 정지 ({stopped}개 정지됨)\n재개: /strategy resume <id>"
    )


@authorized_only
async def _signals(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/signals - 마지막 실행의 시그널 현황"""
    all_signals = bot.executor.last_signals
    if not all_signals:
        await update.message.reply_text(
            "📡 아직 전략 실행 이력이 없습니다.\n(5분 간격 자동 실행 대기 중)"
        )
        return

    lines = ["📡 마지막 실행 시그널", "━━━━━━━━━━━━━━━━━━━━"]
    for strategy_id, signals in all_signals.items():
        strategy = bot.registry.get(strategy_id)
        name = strategy.name if strategy else strategy_id

        if not signals:
            lines.append(f"\n⏸️ {name}: 시그널 없음 (HOLD)")
        else:
            lines.append(f"\n🔔 {name}: {len(signals)}개 시그널")
            for sig in signals:
                emoji = "🟢" if sig["side"] == "buy" else "🔴"
                lines.append(
                    f"  {emoji} {sig['side'].upper()} {sig['symbol']} "
                    f"{format_krw(sig['amount'])} "
                    f"(신뢰도: {sig['confidence']:.0%})"
                )
                lines.append(f"     사유: {sig['reason']}")

    await update.message.reply_text("\n".join(lines))


@authorized_only
async def _risk(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/risk - 리스크 현황"""
    rm = bot.executor._risk
    lines = [
        "🛡️ 리스크 현황",
        "━━━━━━━━━━━━━━━━━━━━",
        f"일일 최대 손실 한도: {rm._max_daily_loss_pct:.0%}",
        f"단일 매매 한도: {rm._max_single_trade_pct:.0%}",
        f"포지션 집중도 한도: {rm._max_position_pct:.0%}",
        "",
        "📊 오늘 전략별 손익:",
    ]

    if not rm._daily_pnl:
        lines.append("  (오늘 매매 없음)")
    else:
        for sid, pnl in rm._daily_pnl.items():
            emoji = "📈" if pnl >= 0 else "📉"
            lines.append(f"  {emoji} {sid}: {format_pnl(pnl)}")

    # 브로커별 포지션 요약
    lines.append("")
    lines.append("📦 보유 포지션:")
    for broker_name, broker in bot.brokers.items():
        try:
            portfolio = await broker.get_portfolio()
            if portfolio.items:
                for item in portfolio.items:
                    pnl_pct = item.pnl_pct * 100
                    emoji = "📈" if pnl_pct >= 0 else "📉"
                    lines.append(
                        f"  {emoji} {item.symbol}: "
                        f"{format_krw(item.value)} ({format_pct(pnl_pct)})"
                    )
            else:
                lines.append(f"  {broker_name}: 보유 없음")
        except Exception as e:
            lines.append(f"  ❌ {broker_name}: {e}")

    await update.message.reply_text("\n".join(lines))


@authorized_only
async def _health(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/health - 시스템 상태"""
    uptime_sec = time.monotonic() - _START_TIME
    hours = int(uptime_sec // 3600)
    minutes = int((uptime_sec % 3600) // 60)

    db_path = "data/trading.db"
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    db_size_kb = db_size / 1024

    strategies = bot.registry.get_all()
    enabled = sum(1 for s in strategies if s.enabled)

    lines = [
        "🏥 시스템 상태",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⏱️ Uptime: {hours}h {minutes}m",
        f"💾 DB 크기: {db_size_kb:.1f} KB",
        f"📋 전략: {enabled}/{len(strategies)} 활성",
        f"🔌 브로커: {', '.join(bot.brokers.keys()) or '없음'}",
    ]

    # 브로커 연결 테스트
    for name, broker in bot.brokers.items():
        try:
            await broker.get_current_price("KRW-BTC")
            lines.append(f"  ✅ {name}: 연결 정상")
        except Exception as e:
            lines.append(f"  ❌ {name}: {e}")

    await update.message.reply_text("\n".join(lines))
