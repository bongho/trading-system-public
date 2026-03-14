from __future__ import annotations

from typing import Any


def format_krw(amount: float) -> str:
    """KRW 금액 포맷 (₩1,234,567)"""
    if amount >= 0:
        return f"₩{amount:,.0f}"
    return f"₩{amount:,.0f}"


def format_pnl(amount: float) -> str:
    """손익 포맷 (+₩1,234 / ₩-1,234)"""
    if amount >= 0:
        return f"+{format_krw(amount)}"
    return format_krw(amount)


def format_pct(pct: float) -> str:
    """퍼센트 포맷 (+12.34% / -5.67%)"""
    if pct >= 0:
        return f"+{pct:.2f}%"
    return f"{pct:.2f}%"


def format_portfolio_text(portfolio: dict[str, Any]) -> str:
    """포트폴리오 텍스트 포맷"""
    lines = [
        f"💰 {portfolio['broker'].upper()} 포트폴리오",
        "━━━━━━━━━━━━━━━━━━━━",
        f"총 자산: {format_krw(portfolio.get('total_value', 0))}",
        f"가용 잔고: {format_krw(portfolio.get('available_balance', 0))}",
    ]

    items = portfolio.get("items", [])
    if items:
        lines.append("")
        lines.append("📊 보유 자산:")
        for item in items:
            pnl_pct = item.get("pnl_pct", 0) * 100
            emoji = "📈" if pnl_pct >= 0 else "📉"
            lines.append(
                f"  {emoji} {item['symbol']}: "
                f"{format_krw(item['value'])} ({format_pct(pnl_pct)})"
            )

    return "\n".join(lines)


def format_trade_notification(trade_info: dict[str, Any]) -> str:
    """매매 알림 메시지 포맷"""
    signal = trade_info.get("signal")
    result = trade_info.get("result")
    strategy = trade_info.get("strategy", "manual")

    if not signal or not result:
        return "매매 정보 없음"

    emoji = "🟢" if signal.side == "buy" else "🔴"
    action = "매수" if signal.side == "buy" else "매도"

    lines = [
        f"{emoji} {action} 체결 [{strategy}]",
        f"종목: {signal.symbol}",
        f"금액: {format_krw(result.amount)}",
        f"가격: {format_krw(result.price)}",
        f"수량: {result.volume:.8f}",
        f"수수료: {format_krw(result.fee)}",
        f"사유: {signal.reason}",
    ]
    return "\n".join(lines)


def format_strategy_status(strategies: list[dict[str, Any]]) -> str:
    """전략 현황 포맷"""
    if not strategies:
        return "등록된 전략이 없습니다."

    lines = ["📋 전략 현황", "━━━━━━━━━━━━━━━━━━━━"]
    for s in strategies:
        status = "🟢" if s.get("enabled") else "⏸️"
        lines.append(
            f"{status} {s['name']} ({s['id']})\n"
            f"   브로커: {s['broker']} | 주기: {s.get('interval_minutes', 5)}분\n"
            f"   자본: {format_krw(s.get('current_capital', 0))} / "
            f"{format_krw(s.get('capital_allocation', 0))}"
        )
    return "\n".join(lines)
