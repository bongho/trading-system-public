from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from src.db.repository import TradeRepository
from src.utils.formatters import format_krw, format_pct, format_pnl

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class DiscordReporter:
    """Discord Webhook을 통한 일간 리포트 + 실시간 매매 알림 전송"""

    def __init__(self, webhook_url: str, trade_repo: TradeRepository) -> None:
        self._webhook_url = webhook_url
        self._trade_repo = trade_repo

    async def _post_webhook(self, payload: dict[str, Any]) -> bool:
        if not self._webhook_url:
            logger.warning("Discord webhook URL not configured")
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._webhook_url, json=payload) as resp:
                    if resp.status != 204:
                        logger.error("Discord webhook failed: %s", await resp.text())
                        return False
                    return True
        except Exception as e:
            logger.error("Discord webhook error: %s", e)
            return False

    async def send_trade_notification(self, trade_info: dict[str, Any]) -> None:
        """실시간 매매 체결 알림"""
        result = trade_info.get("result")
        signal = trade_info.get("signal")
        if not result or not signal:
            return

        side_emoji = "🟢 매수" if signal.side == "buy" else "🔴 매도"
        color = 0x00FF00 if signal.side == "buy" else 0xFF0000

        embed = {
            "title": f"{side_emoji} {signal.symbol}",
            "description": (
                f"**전략**: {trade_info.get('strategy', 'manual')}\n"
                f"**사유**: {signal.reason}\n"
                f"**금액**: {format_krw(result.amount)}\n"
                f"**가격**: {format_krw(result.price)}\n"
                f"**수수료**: {format_krw(result.fee)}\n"
                f"**신뢰도**: {signal.confidence:.0%}"
            ),
            "color": color,
            "timestamp": datetime.now(KST).isoformat(),
        }
        await self._post_webhook({"embeds": [embed]})

    async def send_daily_report(self, strategies: list[dict[str, Any]]) -> None:
        if not self._webhook_url:
            logger.warning("Discord webhook URL not configured")
            return

        embeds = []
        for strategy in strategies:
            embed = await self._build_strategy_embed(strategy)
            embeds.append(embed)

        # Discord embed 제한: 최대 10개/메시지
        for i in range(0, len(embeds), 10):
            batch = embeds[i : i + 10]
            payload: dict[str, Any] = {"embeds": batch}
            if i == 0:
                payload["content"] = (
                    f"📊 **일간 리포트** ({datetime.now(KST).strftime('%Y-%m-%d')})"
                )
            await self._post_webhook(payload)

        logger.info("Daily report sent to Discord (%d strategies)", len(strategies))

    async def _build_strategy_embed(self, strategy: dict[str, Any]) -> dict[str, Any]:
        stats = await self._trade_repo.get_strategy_stats(strategy["id"])
        recent = await self._trade_repo.get_recent_trades(strategy["id"], limit=5)

        total_pnl = stats.get("total_pnl", 0) or 0
        capital = strategy.get("capital_allocation", 100000)
        current = capital + total_pnl
        pnl_pct = (total_pnl / capital * 100) if capital else 0

        win = stats.get("win_count", 0) or 0
        loss = stats.get("loss_count", 0) or 0
        total = stats.get("total_trades", 0) or 0
        win_rate = (win / total * 100) if total > 0 else 0

        avg_profit = stats.get("avg_profit", 0) or 0
        avg_loss = stats.get("avg_loss", 0) or 0
        rr_ratio = abs(avg_profit / avg_loss) if avg_loss else 0
        rr_emoji = "✅ 양호" if rr_ratio >= 1.0 else "⚠️ 개선필요"

        # 색상: 수익=초록, 손실=빨강
        color = 0x00FF00 if total_pnl >= 0 else 0xFF0000

        # 최근 청산 내역
        recent_lines = []
        for t in recent:
            if t["side"] == "sell" and t["pnl"] is not None:
                emoji = "✅" if t["pnl"] > 0 else "❌"
                action = "익절" if t["pnl"] > 0 else "손절"
                ts = t["executed_at"][:16].replace("T", " ")
                pnl_str = format_pnl(t["pnl"])
                pct_str = (
                    format_pct(t.get("pnl_pct", 0) * 100) if t.get("pnl_pct") else ""
                )
                recent_lines.append(
                    f"{emoji} [{ts}] {action} {t['symbol']}  {pnl_str} ({pct_str})"
                )

        description = (
            f"📅 **어제 일간**\n"
            f"  현재 캐파: {format_krw(current)} "
            f"({'📈' if pnl_pct >= 0 else '📉'} {format_pct(pnl_pct)} from {format_krw(capital)})\n\n"
            f"📊 **누적 성과**\n"
            f"  손익: {'📈' if total_pnl >= 0 else '📉'} {format_pnl(total_pnl)} ({format_pct(pnl_pct)})\n"
            f"  승률: {win_rate:.0f}%  ({win}승 {loss}패 / 총 {total}건)\n\n"
            f"📐 **손익비 분석**\n"
            f"  평균 이익: {format_pnl(avg_profit)}\n"
            f"  평균 손실: {format_krw(avg_loss)}\n"
            f"  손익비 (RR): {rr_ratio:.2f} {rr_emoji}\n"
            f"  최대 이익: {format_pnl(stats.get('max_profit', 0) or 0)}\n"
            f"  최대 손실: {format_krw(stats.get('max_loss', 0) or 0)}\n"
        )

        if recent_lines:
            description += "\n🔄 **최근 청산**\n" + "\n".join(recent_lines)

        return {
            "title": f"〈 {strategy['name']} 〉",
            "description": description,
            "color": color,
        }
