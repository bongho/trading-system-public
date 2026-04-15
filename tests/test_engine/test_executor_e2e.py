"""Executor + RiskManager 통합 E2E 테스트.

TestRiskManagerIntegration: 실제 Upbit 포트폴리오 기반 리스크 검증 (주문 없음)
TestExecutorDryRun: SimpleRSI 전략 dry_run 실행 — 실제 API 호출, 주문/DB 기록 없음

실행 조건: .env에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 설정
CI 제외: pytest -m "not e2e"

사용법:
    pytest tests/test_engine/test_executor_e2e.py -v
    pytest tests/test_engine/test_executor_e2e.py -v -k "RiskManager"
    pytest tests/test_engine/test_executor_e2e.py -v -k "DryRun"
"""

from __future__ import annotations

import pytest

from src.brokers.upbit import UpbitAdapter
from src.config import settings
from src.db.schema import init_db
from src.engine.executor import Executor
from src.engine.risk_manager import RiskManager
from src.strategies.base import TradeSignal
from src.strategies.simple_rsi import create_strategy
from src.db.repository import TradeRepository

pytestmark = pytest.mark.e2e


def _require_credentials() -> UpbitAdapter:
    if not settings.upbit_access_key:
        pytest.skip("UPBIT_ACCESS_KEY not configured")
    return UpbitAdapter(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
    )


class TestRiskManagerIntegration:
    """실제 포트폴리오 조회 기반 RiskManager 검증 — 주문 없음."""

    async def test_rejects_excessive_single_trade(self) -> None:
        """단일 매매 금액이 자본금 100% → 거부."""
        adapter = _require_credentials()
        try:
            portfolio = await adapter.get_portfolio()
            capital = max(portfolio.total_value, 1.0)  # 0이면 나눗셈 방지

            rm = RiskManager(max_single_trade_pct=0.20)
            signal = TradeSignal(
                side="buy",
                symbol="KRW-BTC",
                amount=capital,  # 100% → 초과
                confidence=0.9,
                reason="과도한 금액 테스트",
            )
            result = rm.validate(signal, "test_strategy", capital, {})
            assert not result.approved
            assert "단일 매매 금액 초과" in result.reason
        finally:
            await adapter.close()

    async def test_rejects_low_confidence(self) -> None:
        """신뢰도 0.1 신호 → 거부 (최소 0.3 미달)."""
        rm = RiskManager()
        signal = TradeSignal(
            side="buy",
            symbol="KRW-BTC",
            amount=10_000,
            confidence=0.1,
            reason="낮은 신뢰도 테스트",
        )
        result = rm.validate(signal, "test_strategy", 1_000_000, {})
        assert not result.approved
        assert "신뢰도" in result.reason

    async def test_approves_valid_signal(self) -> None:
        """실제 잔고 기준 10% 매수 + confidence 0.8 → 승인."""
        adapter = _require_credentials()
        try:
            portfolio = await adapter.get_portfolio()
            capital = max(portfolio.total_value, 10_000.0)

            rm = RiskManager(max_single_trade_pct=0.20)
            signal = TradeSignal(
                side="buy",
                symbol="KRW-ETH",  # 보유 중이 아닐 가능성 높은 종목
                amount=capital * 0.10,
                confidence=0.8,
                reason="유효 신호 테스트",
            )
            # 기존 포지션 없는 상태에서 검증
            result = rm.validate(signal, "test_strategy", capital, {})
            assert result.approved, f"승인 실패: {result.reason}"
        finally:
            await adapter.close()

    async def test_rejects_existing_position(self) -> None:
        """이미 보유 중인 종목 재매수 → 거부."""
        rm = RiskManager()
        signal = TradeSignal(
            side="buy",
            symbol="KRW-BTC",
            amount=10_000,
            confidence=0.9,
            reason="중복 포지션 테스트",
        )
        # KRW-BTC를 이미 보유 중인 상황
        current_positions = {"KRW-BTC": 0.001}
        result = rm.validate(signal, "test_strategy", 1_000_000, current_positions)
        assert not result.approved
        assert "포지션 보유" in result.reason


class TestExecutorDryRun:
    """Executor dry_run=True — 실제 API 호출, 주문/DB 기록 없음."""

    async def test_run_strategy_completes_without_error(self) -> None:
        """SimpleRSI dry_run 1회 실행 — 에러 없이 완료, 결과는 list."""
        adapter = _require_credentials()
        db = await init_db(":memory:")
        try:
            trade_repo = TradeRepository(db)
            rm = RiskManager()
            executor = Executor(
                brokers={"upbit": adapter},
                risk_manager=rm,
                trade_repo=trade_repo,
                dry_run=True,
            )
            strategy = create_strategy(capital_allocation=100_000)

            results = await executor.run_strategy(strategy)

            # 신호 생성 여부와 무관하게 list 반환이 보장되어야 함
            assert isinstance(results, list)
            # dry_run 결과라면 모두 dry_run=True 마크
            for r in results:
                assert r.get("dry_run") is True, "dry_run 모드인데 실제 trade가 반환됨"
        finally:
            await adapter.close()
            await db.close()

    async def test_no_trade_recorded_in_db(self) -> None:
        """dry_run 실행 후 DB trades 테이블에 기록이 없어야 함."""
        adapter = _require_credentials()
        db = await init_db(":memory:")
        try:
            trade_repo = TradeRepository(db)
            rm = RiskManager()
            executor = Executor(
                brokers={"upbit": adapter},
                risk_manager=rm,
                trade_repo=trade_repo,
                dry_run=True,
            )
            strategy = create_strategy(capital_allocation=100_000)

            await executor.run_strategy(strategy)

            # DB에 trades 레코드가 없어야 함
            cursor = await db.execute("SELECT COUNT(*) FROM trades")
            row = await cursor.fetchone()
            count = row[0]
            assert count == 0, f"dry_run인데 DB에 {count}개 거래 기록됨"
        finally:
            await adapter.close()
            await db.close()
