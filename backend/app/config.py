from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TechBref"
    database_url: str = "sqlite:///./techbref.db"
    timezone: str = "Asia/Karachi"
    scheduled_time: str = "09:00"
    schedule_enabled: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    tavily_api_key: str | None = None
    request_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    articles_per_category: int = Field(default=7, ge=1, le=20)
    rss_feeds_json: str | None = None
    admin_api_token: str | None = Field(default=None, min_length=32)
    admin_user_id: str = Field(default="admin", min_length=1, max_length=180)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("TIMEZONE must be a valid IANA timezone") from error
        return value

    @field_validator("scheduled_time")
    @classmethod
    def validate_scheduled_time(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("SCHEDULED_TIME must use 24-hour HH:MM format")
        return value

    @field_validator("admin_api_token", mode="before")
    @classmethod
    def normalize_admin_api_token(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        token = str(value).strip()
        if len(token) < 32:
            raise ValueError("ADMIN_API_TOKEN must contain at least 32 characters")
        return token


@lru_cache
def get_settings() -> Settings:
    return Settings()
