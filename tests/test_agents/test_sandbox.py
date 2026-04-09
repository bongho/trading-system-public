"""Sandbox 격리 환경 테스트."""

from __future__ import annotations

import pytest
from pathlib import Path

from src.agents.sandbox import Sandbox
from src.strategies.base import BacktestResult


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    sandbox_dir = tmp_path / "sandbox"
    strategies_dir = tmp_path / "strategies"
    sandbox_dir.mkdir()
    strategies_dir.mkdir()
    return sandbox_dir, strategies_dir


@pytest.fixture
def sandbox(tmp_dirs):
    sandbox_dir, strategies_dir = tmp_dirs
    return Sandbox(sandbox_dir=sandbox_dir, strategies_dir=strategies_dir)


class TestSandbox:
    def test_prepare_copies_file(self, sandbox: Sandbox, tmp_dirs) -> None:
        _, strategies_dir = tmp_dirs
        # 전략 파일 생성
        (strategies_dir / "test_strat.py").write_text("# original", encoding="utf-8")

        dest = sandbox.prepare("test_strat")
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "# original"

    def test_prepare_missing_file_raises(self, sandbox: Sandbox) -> None:
        with pytest.raises(FileNotFoundError):
            sandbox.prepare("nonexistent")

    def test_apply_code_diff(self, sandbox: Sandbox) -> None:
        code = "def create_strategy(): pass"
        dest = sandbox.apply_code_diff("my_strat", code)
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == code

    def test_validate_and_promote_success(self, sandbox: Sandbox, tmp_dirs) -> None:
        sandbox_dir, strategies_dir = tmp_dirs
        # sandbox에 파일 생성
        (sandbox_dir / "strat_a.py").write_text("# improved", encoding="utf-8")
        # 기존 strategies에 파일 생성
        (strategies_dir / "strat_a.py").write_text("# original", encoding="utf-8")

        before = BacktestResult(total_pnl=100, max_drawdown=5, sharpe_ratio=1.0)
        after = BacktestResult(total_pnl=120, max_drawdown=5, sharpe_ratio=1.1)

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.validate_and_promote("strat_a", before, after)
        )
        assert result is True
        # 프로모션 확인
        assert (strategies_dir / "strat_a.py").read_text(encoding="utf-8") == "# improved"
        # 백업 확인
        assert (strategies_dir / "strat_a.py.bak").exists()

    def test_validate_and_promote_fail_pnl_drop(self, sandbox: Sandbox, tmp_dirs) -> None:
        sandbox_dir, _ = tmp_dirs
        (sandbox_dir / "strat_b.py").write_text("# bad", encoding="utf-8")

        before = BacktestResult(total_pnl=100, max_drawdown=5, sharpe_ratio=1.0)
        after = BacktestResult(total_pnl=50, max_drawdown=5, sharpe_ratio=1.0)  # PnL 50% 하락

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            sandbox.validate_and_promote("strat_b", before, after)
        )
        assert result is False

    def test_cleanup(self, sandbox: Sandbox, tmp_dirs) -> None:
        sandbox_dir, _ = tmp_dirs
        (sandbox_dir / "strat_c.py").write_text("# temp", encoding="utf-8")
        sandbox.cleanup("strat_c")
        assert not (sandbox_dir / "strat_c.py").exists()

    def test_cleanup_nonexistent(self, sandbox: Sandbox) -> None:
        # cleanup on nonexistent file should not raise
        sandbox.cleanup("does_not_exist")
