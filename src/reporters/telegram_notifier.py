from __future__ import annotations

import logging
from typing import Any

from src.utils.formatters import format_trade_notification

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, send_func) -> None:
        self._send = send_func

    async def notify_trade(self, trade_info: dict[str, Any]) -> None:
        text = format_trade_notification(trade_info)
        await self._send(text)

    async def notify_error(self, message: str) -> None:
        await self._send(f"🚨 에러: {message}")

    async def notify_system(self, message: str) -> None:
        await self._send(f"ℹ️ {message}")
