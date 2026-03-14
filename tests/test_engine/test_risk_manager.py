from __future__ import annotations

from src.engine.risk_manager import RiskManager
from src.strategies.base import TradeSignal


def test_validate_approved():
    rm = RiskManager(max_daily_loss_pct=0.10, max_position_pct=0.30, max_single_trade_pct=0.20)
    signal = TradeSignal(
        side="buy", symbol="KRW-BTC", amount=10000, confidence=0.5, reason="test"
    )
    result = rm.validate(signal, "test_strategy", 100000, {})
    assert result.approved


def test_validate_low_confidence():
    rm = RiskManager()
    signal = TradeSignal(
        side="buy", symbol="KRW-BTC", amount=10000, confidence=0.1, reason="test"
    )
    result = rm.validate(signal, "test_strategy", 100000, {})
    assert not result.approved
    assert "신뢰도" in result.reason


def test_validate_daily_loss_exceeded():
    rm = RiskManager(max_daily_loss_pct=0.05)
    rm.record_pnl("test_strategy", -6000)  # 6% loss on 100k

    signal = TradeSignal(
        side="buy", symbol="KRW-BTC", amount=10000, confidence=0.5, reason="test"
    )
    result = rm.validate(signal, "test_strategy", 100000, {})
    assert not result.approved
    assert "일일 최대 손실" in result.reason


def test_validate_existing_position():
    rm = RiskManager()
    signal = TradeSignal(
        side="buy", symbol="KRW-BTC", amount=10000, confidence=0.5, reason="test"
    )
    result = rm.validate(signal, "test_strategy", 100000, {"KRW-BTC": 0.1})
    assert not result.approved
    assert "포지션 보유" in result.reason


def test_validate_single_trade_exceeded():
    rm = RiskManager(max_single_trade_pct=0.10)
    signal = TradeSignal(
        side="buy", symbol="KRW-BTC", amount=15000, confidence=0.5, reason="test"
    )
    result = rm.validate(signal, "test_strategy", 100000, {})
    assert not result.approved
    assert "단일 매매 금액" in result.reason


def test_reset_daily():
    rm = RiskManager()
    rm.record_pnl("test", -5000)
    rm.reset_daily()
    assert rm._daily_pnl == {}
