"""AI 에이전트 Telegram 핸들러 — Phase 5.

/ai analyze <symbol> — 시장 분석
/ai optimize [strategy] — 전략 최적화 (Evaluator-Optimizer 루프)
/ai review [strategy] — 전략 검토
/ai status — 대기 중인 제안 확인
/ai confirm <session_id> — 제안 적용
/ai cancel <session_id> — 제안 취소
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only
from src.utils.formatters import format_krw, format_pct

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)


def register_ai_handlers(bot: TradingBot) -> None:
    from telegram.ext import CommandHandler

    def _make(handler):
        @authorized_only
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await handler(update, context, bot)

        return wrapper

    bot.app.add_handler(CommandHandler("ai", _make(_ai_router)))


@authorized_only
async def _ai_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE, bot: TradingBot
) -> None:
    """메인 라우터: /ai <subcommand> [args...]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(_usage())
        return

    orchestrator = getattr(bot, "orchestrator", None)
    if not orchestrator:
        await update.message.reply_text("❌ AI 에이전트가 설정되지 않았습니다.")
        return

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "analyze":
        await _analyze(update, sub_args, bot)
    elif subcommand == "optimize":
        await _optimize(update, sub_args, bot)
    elif subcommand == "review":
        await _review(update, sub_args, bot)
    elif subcommand == "status":
        await _status(update, bot)
    elif subcommand == "confirm":
        await _confirm(update, sub_args, bot)
    elif subcommand == "cancel":
        await _cancel(update, sub_args, bot)
    else:
        await update.message.reply_text(f"❌ 알 수 없는 명령: {subcommand}\n\n{_usage()}")


async def _analyze(
    update: Update, args: list[str], bot: TradingBot
) -> None:
    """/ai analyze <symbol>"""
    if not args:
        await update.message.reply_text("사용법: /ai analyze <symbol>\n예시: /ai analyze KRW-BTC")
        return

    symbol = args[0].upper()
    await update.message.reply_text(f"🔍 {symbol} 분석 중...")

    result = await bot.orchestrator.analyze(symbol)

    sentiment_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(
        result.sentiment, "⚪"
    )

    text = (
        f"{sentiment_emoji} **{result.symbol} 분석 결과**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"심리: {result.sentiment.upper()} (신뢰도: {result.confidence:.0%})\n\n"
        f"📝 {result.summary}\n"
    )

    if result.indicators:
        text += "\n📊 지표\n"
        for k, v in result.indicators.items():
            if v is not None:
                text += f"  {k}: {v}\n"

    if result.key_levels:
        text += "\n📐 주요 레벨\n"
        for k, v in result.key_levels.items():
            text += f"  {k}: {format_krw(v)}\n"

    await update.message.reply_text(text)


async def _optimize(
    update: Update, args: list[str], bot: TradingBot
) -> None:
    """/ai optimize [strategy_id]"""
    # 기본값: 첫 번째 활성 전략
    if args:
        strategy_id = args[0]
    else:
        strategies = bot.registry.get_enabled()
        if not strategies:
            await update.message.reply_text("❌ 활성화된 전략이 없습니다.")
            return
        strategy_id = strategies[0].id

    strategy = bot.registry.get(strategy_id)
    if not strategy:
        await update.message.reply_text(f"❌ 전략을 찾을 수 없음: {strategy_id}")
        return

    async def notify(msg: str) -> None:
        await update.message.reply_text(msg)

    result = await bot.orchestrator.optimize(strategy_id, notify_callback=notify)

    if result.success and result.review and result.review.approved:
        text = (
            f"✅ **최적화 제안 승인** (Round {result.rounds})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"전략: {strategy_id}\n"
            f"리스크: {result.review.risk_score:.0%}\n\n"
        )
        if result.proposal and result.proposal.param_changes:
            text += "📝 파라미터 변경\n"
            for k, v in result.proposal.param_changes.items():
                old = strategy.params.get(k, "?")
                text += f"  {k}: {old} → {v}\n"
        if result.proposal and result.proposal.code_diff:
            text += "\n📄 코드 변경 있음\n"
        if result.proposal:
            text += f"\n💡 {result.proposal.expected_improvement}\n"
        text += (
            f"\n⏳ 적용하려면:\n"
            f"/ai confirm {result.session_id}\n"
            f"/ai cancel {result.session_id}"
        )
    elif result.success and result.error:
        text = f"ℹ️ {result.error}"
    else:
        text = (
            f"❌ **최적화 실패** (Round {result.rounds})\n"
            f"사유: {result.error}\n"
        )
        if result.review:
            text += f"\n마지막 리뷰:\n"
            text += f"  리스크: {result.review.risk_score:.0%}\n"
            for c in result.review.concerns:
                text += f"  ⚠️ {c}\n"
            if result.review.feedback:
                text += f"\n피드백: {result.review.feedback}"

    await update.message.reply_text(text)


async def _review(
    update: Update, args: list[str], bot: TradingBot
) -> None:
    """/ai review [strategy_id]"""
    if args:
        strategy_id = args[0]
    else:
        strategies = bot.registry.get_enabled()
        if not strategies:
            await update.message.reply_text("❌ 활성화된 전략이 없습니다.")
            return
        strategy_id = strategies[0].id

    await update.message.reply_text(f"🔎 {strategy_id} 검토 중...")
    result = await bot.orchestrator.review_strategy(strategy_id)

    status = "✅ 양호" if result.approved else "⚠️ 주의 필요"
    text = (
        f"{status} **{strategy_id} 검토 결과**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"리스크 점수: {result.risk_score:.0%}\n"
    )
    if result.concerns:
        text += "\n⚠️ 우려사항\n"
        for c in result.concerns:
            text += f"  • {c}\n"
    if result.feedback:
        text += f"\n📝 {result.feedback}"

    await update.message.reply_text(text)


async def _status(update: Update, bot: TradingBot) -> None:
    """/ai status — 대기 중인 제안 목록"""
    pending = bot.orchestrator.get_pending_proposals()
    if not pending:
        await update.message.reply_text("📋 대기 중인 AI 제안이 없습니다.")
        return

    text = f"📋 **대기 중인 제안** ({len(pending)}건)\n━━━━━━━━━━━━━━━━━━━━\n"
    for sid, result in pending.items():
        strategy = result.proposal.strategy_id if result.proposal else "?"
        text += (
            f"\n🔹 {sid}\n"
            f"  전략: {strategy}\n"
            f"  /ai confirm {sid}\n"
            f"  /ai cancel {sid}\n"
        )
    await update.message.reply_text(text)


async def _confirm(
    update: Update, args: list[str], bot: TradingBot
) -> None:
    """/ai confirm <session_id>"""
    if not args:
        await update.message.reply_text("사용법: /ai confirm <session_id>")
        return

    session_id = args[0]
    await update.message.reply_text("⏳ 제안 적용 중...")

    success = await bot.orchestrator.confirm_proposal(session_id)
    if success:
        await update.message.reply_text("✅ 제안이 성공적으로 적용되었습니다.")
    else:
        await update.message.reply_text("❌ 제안을 찾을 수 없거나 적용에 실패했습니다.")


async def _cancel(
    update: Update, args: list[str], bot: TradingBot
) -> None:
    """/ai cancel <session_id>"""
    if not args:
        await update.message.reply_text("사용법: /ai cancel <session_id>")
        return

    session_id = args[0]
    if bot.orchestrator.cancel_proposal(session_id):
        await update.message.reply_text(f"🗑️ 제안 취소됨: {session_id}")
    else:
        await update.message.reply_text("❌ 대기 중인 제안을 찾을 수 없습니다.")


def _usage() -> str:
    return """🤖 AI 에이전트 명령어

/ai analyze <symbol> — 시장 분석
/ai optimize [strategy] — 전략 최적화
/ai review [strategy] — 전략 검토
/ai status — 대기 중인 제안
/ai confirm <id> — 제안 적용
/ai cancel <id> — 제안 취소"""
