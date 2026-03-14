from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Upbit
    upbit_access_key: str = ""
    upbit_secret_key: str = ""

    # Kiwoom (Phase 4)
    kiwoom_app_key: str = ""
    kiwoom_app_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Discord (Phase 3)
    discord_webhook_url: str = ""

    # Anthropic (Phase 5)
    anthropic_api_key: str = ""

    # Database
    db_path: str = "data/trading.db"

    # Trading
    max_daily_loss_pct: float = Field(default=0.10, description="일일 최대 손실률")
    max_position_pct: float = Field(default=0.30, description="전략당 최대 포지션 비율")
    default_interval_minutes: int = 5


settings = Settings()
