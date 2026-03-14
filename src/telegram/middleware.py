from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any

from telegram.ext import ContextTypes

from src.config import settings
from telegram import Update

logger = logging.getLogger(__name__)


def authorized_only(
    func: Callable[..., Coroutine[Any, Any, None]],
) -> Callable[..., Coroutine[Any, Any, None]]:
    """허가된 chat_id만 접근 허용"""

    @wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> None:
        chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        if chat_id != settings.telegram_chat_id:
            logger.warning("Unauthorized access from chat_id: %s", chat_id)
            if update.message:
                await update.message.reply_text("⛔ 인증되지 않은 사용자입니다.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper
