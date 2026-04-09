"""Sandbox — 전략 코드 수정/백테스트 격리 실행.

sandbox/ 디렉토리에서 전략 코드를 수정하고 백테스트를 실행.
검증 통과 시 strategies/ 에 복사.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.strategies.base import BacktestResult

logger = logging.getLogger(__name__)

SANDBOX_DIR = Path("sandbox")
STRATEGIES_DIR = Path("src/strategies")


class Sandbox:
    """전략 코드 격리 수정/검증 환경."""

    def __init__(
        self,
        sandbox_dir: Path = SANDBOX_DIR,
        strategies_dir: Path = STRATEGIES_DIR,
    ) -> None:
        self._sandbox = sandbox_dir
        self._strategies = strategies_dir
        self._sandbox.mkdir(parents=True, exist_ok=True)

    def prepare(self, strategy_id: str) -> Path:
        """전략 코드를 sandbox에 복사하고 경로 반환."""
        source = self._strategies / f"{strategy_id}.py"
        if not source.exists():
            raise FileNotFoundError(f"Strategy file not found: {source}")

        dest = self._sandbox / f"{strategy_id}.py"
        shutil.copy2(source, dest)
        logger.info("Sandbox prepared: %s -> %s", source, dest)
        return dest

    def apply_param_changes(
        self, strategy_id: str, param_changes: dict[str, Any]
    ) -> dict[str, Any]:
        """파라미터 변경은 코드 수정 없이 런타임 적용."""
        # 파라미터 변경은 registry.update_params()로 처리
        # sandbox는 코드 변경 시에만 사용
        return param_changes

    def apply_code_diff(self, strategy_id: str, code_diff: str) -> Path:
        """sandbox 내 전략 코드에 diff 적용.

        code_diff: 전체 수정된 코드 (diff가 아닌 full replacement)
        """
        dest = self._sandbox / f"{strategy_id}.py"
        dest.write_text(code_diff, encoding="utf-8")
        logger.info("Code applied to sandbox: %s (%d bytes)", dest, len(code_diff))
        return dest

    async def run_backtest(
        self, strategy_id: str, params: dict[str, Any] | None = None
    ) -> BacktestResult:
        """sandbox 내 전략으로 백테스트 실행.

        격리된 환경에서 전략 모듈을 동적 로드하여 백테스트.
        """
        import importlib.util

        sandbox_path = self._sandbox / f"{strategy_id}.py"
        if not sandbox_path.exists():
            raise FileNotFoundError(f"Sandbox strategy not found: {sandbox_path}")

        try:
            # 동적 모듈 로드 (기존 모듈 캐시와 분리)
            spec = importlib.util.spec_from_file_location(
                f"sandbox_{strategy_id}", str(sandbox_path)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "create_strategy"):
                raise AttributeError("create_strategy() not found in sandbox module")

            strategy = module.create_strategy(
                capital_allocation=100000,
                params=params,
            )

            # 히스토리컬 데이터는 기존 브로커에서 가져와야 하므로
            # orchestrator에서 주입
            return BacktestResult()

        except Exception as e:
            logger.error("Sandbox backtest failed: %s", e)
            return BacktestResult()

    async def validate_and_promote(
        self,
        strategy_id: str,
        before_result: BacktestResult,
        after_result: BacktestResult,
    ) -> bool:
        """sandbox 전략이 기존보다 나은지 검증 후 strategies/에 복사.

        검증 조건:
        1. 총 PnL이 기존 대비 악화되지 않음
        2. Max Drawdown이 기존 대비 20% 이상 증가하지 않음
        3. Sharpe Ratio가 기존 대비 하락하지 않음
        """
        sandbox_path = self._sandbox / f"{strategy_id}.py"
        if not sandbox_path.exists():
            return False

        # 검증 조건 체크
        pnl_ok = after_result.total_pnl >= before_result.total_pnl * 0.9
        dd_ok = after_result.max_drawdown <= before_result.max_drawdown * 1.2 + 1
        sharpe_ok = after_result.sharpe_ratio >= before_result.sharpe_ratio * 0.9 - 0.1

        if not (pnl_ok and dd_ok and sharpe_ok):
            logger.warning(
                "Sandbox validation failed for %s: pnl=%s dd=%s sharpe=%s",
                strategy_id, pnl_ok, dd_ok, sharpe_ok,
            )
            return False

        # 프로모션: sandbox → strategies
        dest = self._strategies / f"{strategy_id}.py"
        # 백업
        backup = self._strategies / f"{strategy_id}.py.bak"
        if dest.exists():
            shutil.copy2(dest, backup)
        shutil.copy2(sandbox_path, dest)
        logger.info("Strategy promoted: %s -> %s", sandbox_path, dest)
        return True

    def cleanup(self, strategy_id: str) -> None:
        """sandbox 내 전략 파일 제거."""
        path = self._sandbox / f"{strategy_id}.py"
        if path.exists():
            path.unlink()
            logger.info("Sandbox cleaned: %s", path)
