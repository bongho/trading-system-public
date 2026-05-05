"""Hunting Mode Telegram 핸들러.

/hunt watch <target_pct> [max_dd_pct]   — 기존 전략 모니터링 + 알림
/hunt start <budget> <target_pct> [max_dd_pct] — 예산 할당 + 자동 교체
/hunt stop                               — 중지
/hunt status                             — 현재 상태
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.engine.hunting import HuntingLoop, HuntingSession
from src.telegram.middleware import authorized_only
from src.utils.formatters import format_krw, format_pct

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)


def register_hunt_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    def _make(handler):
        @authorized_only
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await handler(update, context, bot)
        return wrapper

    bot.app.add_handler(CommandHandler("hunt", _make(_hunt_router)))
    bot.app.add_handler(CommandHandler("upbit", _make(_upbit_recommend)))


@authorized_only
async def _hunt_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """메인 라우터: /hunt <subcommand> [args...]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(_usage())
        return

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "watch":
        await _watch(update, sub_args, bot)
    elif subcommand == "start":
        await _start(update, sub_args, bot)
    elif subcommand == "stop":
        await _stop(update, bot)
    elif subcommand == "status":
        await _status(update, bot)
    else:
        await update.message.reply_text(f"❌ 알 수 없는 명령: {subcommand}\n\n{_usage()}")


async def _watch(update: Update, args: list[str], bot: TradingBot) -> None:
    """/hunt watch <target_pct> [max_dd_pct]"""
    if not args:
        await update.message.reply_text(
            "사용법: /hunt watch <target_pct> [max_dd_pct]\n"
            "예시: /hunt watch 10 5  (목표 10%, 낙폭 5%)"
        )
        return

    if getattr(bot, "hunter", None) and bot.hunter.session.status == "running":
        await update.message.reply_text("❌ 이미 실행 중입니다. /hunt stop 으로 중지 후 재시작.")
        return

    try:
        target_pct = float(args[0])
        max_dd_pct = float(args[1]) if len(args) > 1 else target_pct / 2
    except ValueError:
        await update.message.reply_text("❌ 숫자로 입력하세요.")
        return

    if not await _check_orchestrator(bot, update):
        return

    session = HuntingSession(
        mode="watch",
        target_return=target_pct / 100,
        max_drawdown=max_dd_pct / 100,
        budget=0,
        eval_interval_hours=1,
    )

    bot.hunter = HuntingLoop(
        session=session,
        orchestrator=bot.orchestrator,
        scheduler=getattr(bot, "scheduler", None),
        registry=bot.registry,
        trade_repo=bot.trade_repo,
        strategy_repo=bot.strategy_repo,
        notify_callback=bot.send_message,
    )
    await bot.hunter.start()

    strategies = bot.registry.get_enabled()
    await update.message.reply_text(
        f"👁️ Watch 모드 시작\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"목표 수익률: {format_pct(target_pct)}\n"
        f"최대 낙폭: -{max_dd_pct:.1f}%\n"
        f"모니터링 전략: {len(strategies)}개\n"
        f"평가 주기: 1시간\n\n"
        f"❓ 중지: /hunt stop"
    )


async def _start(update: Update, args: list[str], bot: TradingBot) -> None:
    """/hunt start <budget> <target_pct> [max_dd_pct]"""
    if len(args) < 2:
        await update.message.reply_text(
            "사용법: /hunt start <budget_krw> <target_pct> [max_dd_pct]\n"
            "예시: /hunt start 500000 10 5\n"
            "  → 전략당 예산 50만원, 목표 10%, 낙폭 5%"
        )
        return

    if getattr(bot, "hunter", None) and bot.hunter.session.status == "running":
        await update.message.reply_text("❌ 이미 실행 중입니다. /hunt stop 으로 중지 후 재시작.")
        return

    try:
        budget = float(args[0])
        target_pct = float(args[1])
        max_dd_pct = float(args[2]) if len(args) > 2 else target_pct / 2
    except ValueError:
        await update.message.reply_text("❌ 숫자로 입력하세요.")
        return

    if budget < 10000:
        await update.message.reply_text("❌ 예산은 최소 10,000원 이상이어야 합니다.")
        return

    if not await _check_orchestrator(bot, update):
        return

    if not getattr(bot, "scheduler", None):
        await update.message.reply_text("❌ 스케줄러가 연결되지 않았습니다.")
        return

    session = HuntingSession(
        mode="hunt",
        target_return=target_pct / 100,
        max_drawdown=max_dd_pct / 100,
        budget=budget,
        eval_interval_hours=1,
    )

    bot.hunter = HuntingLoop(
        session=session,
        orchestrator=bot.orchestrator,
        scheduler=bot.scheduler,
        registry=bot.registry,
        trade_repo=bot.trade_repo,
        strategy_repo=bot.strategy_repo,
        notify_callback=bot.send_message,
    )
    await bot.hunter.start()

    await update.message.reply_text(
        f"🏹 자동사냥 시작!\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"전략당 예산: {format_krw(budget)}\n"
        f"목표 수익률: {format_pct(target_pct)}\n"
        f"최대 낙폭: -{max_dd_pct:.1f}%\n"
        f"평가 주기: 1시간\n\n"
        f"🤖 초기 전략을 생성 중입니다...\n"
        f"잠시 후 Telegram 알림으로 결과를 보내드립니다.\n\n"
        f"❓ 중지: /hunt stop\n"
        f"❓ 상태: /hunt status"
    )


async def _stop(update: Update, bot: TradingBot) -> None:
    """/hunt stop"""
    hunter = getattr(bot, "hunter", None)
    if not hunter or hunter.session.status not in ("running",):
        await update.message.reply_text("📋 실행 중인 사냥 모드가 없습니다.")
        return

    mode = hunter.session.mode
    replaced = hunter.session.replaced_count
    await hunter.stop()

    if mode == "hunt":
        # 소유 전략 모두 pause
        for sid in hunter.session.strategy_ids:
            bot.registry.set_enabled(sid, False)
            await bot.strategy_repo.set_enabled(sid, False)
        pause_text = f"\n소유 전략 {len(hunter.session.strategy_ids)}개 일시 정지됨"
    else:
        pause_text = ""

    await update.message.reply_text(
        f"🛑 사냥 모드 중지\n"
        f"  모드: {mode}\n"
        f"  전략 교체: {replaced}회"
        f"{pause_text}"
    )


async def _status(update: Update, bot: TradingBot) -> None:
    """/hunt status"""
    hunter = getattr(bot, "hunter", None)
    if not hunter:
        await update.message.reply_text("📋 사냥 모드 미실행 상태입니다.")
        return

    info = hunter.get_status()
    status_emoji = {
        "running": "🟢",
        "stopped": "⏹️",
        "target_reached": "🎯",
        "drawdown_exceeded": "🛑",
    }.get(info["status"], "❓")

    lines = [
        f"{status_emoji} 사냥 모드 상태",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"모드: {info['mode']} | 상태: {info['status']}",
        f"목표: {info['target_return']:.1%} | 낙폭 한도: {info['max_drawdown']:.1%}",
        f"경과: {info['elapsed']} | 전략 교체: {info['replaced_count']}회",
    ]

    if info["mode"] == "hunt" and info["strategy_ids"]:
        lines.append("")
        lines.append("📋 소유 전략:")
        for sid in info["strategy_ids"]:
            db_data = await bot.strategy_repo.get_strategy(sid)
            if db_data:
                alloc = float(db_data.get("capital_allocation", 0))
                current = float(db_data.get("current_capital", alloc))
                pnl_pct = (current - alloc) / alloc * 100 if alloc else 0
                enabled = "▶️" if db_data.get("enabled") else "⏸️"
                lines.append(f"  {enabled} {sid}: {format_pct(pnl_pct)}")
    elif info["mode"] == "watch":
        strategies = bot.registry.get_enabled()
        lines.append(f"모니터링 전략: {len(strategies)}개")

    await update.message.reply_text("\n".join(lines))


async def _check_orchestrator(bot: TradingBot, update: Update) -> bool:
    if not getattr(bot, "orchestrator", None):
        await update.message.reply_text("❌ AI 에이전트가 설정되지 않았습니다.")
        return False
    return True


def _usage() -> str:
    return """🏹 Hunt Mode 명령어

/hunt watch <target_pct> [max_dd_pct]
  → 기존 전략 모니터링 + 알림
  예: /hunt watch 10 5

/hunt start <budget_krw> <target_pct> [max_dd_pct]
  → 자동 전략 생성 + 교체
  예: /hunt start 500000 10 5

/hunt status  — 현재 상태
/hunt stop    — 중지"""


async def _upbit_recommend(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """/upbit — 업비트 단타 추천 (v3, 즉시 조회)"""
    await update.message.reply_text("🔍 업비트 단타 추천 조회 중...")
    script = Path("/app/skills/crypto-recommender/scripts/recommend.py")
    if not script.exists():
        await update.message.reply_text("❌ 추천 스크립트를 찾을 수 없습니다.")
        return
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--exchange", "upbit", "--market", "spot"],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(result.stdout)
        top3 = data.get("top3", [])
    except Exception as e:
        logger.error("Upbit recommend failed: %s", e)
        await update.message.reply_text(f"⚠️ 조회 실패: {e}")
        return

    if not top3:
        await update.message.reply_text("⚠️ 현재 조건을 충족하는 코인 없음 (RSI·거래량 필터 미달)")
        return

    lines = ["📊 <b>업비트 단타 추천 (v3)</b>\n"]
    for i, r in enumerate(top3, 1):
        lines.append(
            f"{i}. {r['market']} | 1h: {r['change_1h']:+.1f}% | "
            f"15m: {r['change_15m']:+.1f}% | RSI: {r['rsi_14']} | 급등: {r['vol_ratio']:.1f}×"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
