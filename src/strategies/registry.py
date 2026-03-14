from __future__ import annotations

import importlib
import logging
from typing import Any

from src.strategies.base import Strategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        self._strategies[strategy.id] = strategy
        logger.info("Registered strategy: %s (%s)", strategy.id, strategy.name)

    def unregister(self, strategy_id: str) -> None:
        if strategy_id in self._strategies:
            del self._strategies[strategy_id]
            logger.info("Unregistered strategy: %s", strategy_id)

    def get(self, strategy_id: str) -> Strategy | None:
        return self._strategies.get(strategy_id)

    def get_all(self) -> list[Strategy]:
        return list(self._strategies.values())

    def get_enabled(self) -> list[Strategy]:
        return [s for s in self._strategies.values() if s.enabled]

    def get_by_broker(self, broker: str) -> list[Strategy]:
        return [s for s in self._strategies.values() if s.broker == broker]

    def set_enabled(self, strategy_id: str, enabled: bool) -> bool:
        strategy = self._strategies.get(strategy_id)
        if strategy:
            strategy.enabled = enabled
            logger.info(
                "Strategy %s %s", strategy_id, "enabled" if enabled else "disabled"
            )
            return True
        return False

    def update_params(self, strategy_id: str, params: dict[str, Any]) -> bool:
        strategy = self._strategies.get(strategy_id)
        if strategy:
            strategy.update_params(params)
            logger.info("Strategy %s params updated: %s", strategy_id, params)
            return True
        return False

    def reload_strategy(self, strategy_id: str, module_path: str) -> bool:
        """Hot-reload: 전략 모듈을 다시 로드하고 인스턴스 교체"""
        try:
            module = importlib.import_module(module_path)
            importlib.reload(module)

            old = self._strategies.get(strategy_id)
            if old and hasattr(module, "create_strategy"):
                new_strategy = module.create_strategy(
                    capital_allocation=old.capital_allocation,
                    params=old.params,
                )
                new_strategy.enabled = old.enabled
                self._strategies[strategy_id] = new_strategy
                logger.info("Hot-reloaded strategy: %s", strategy_id)
                return True
        except Exception as e:
            logger.error("Failed to reload strategy %s: %s", strategy_id, e)
        return False
