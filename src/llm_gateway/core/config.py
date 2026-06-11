"""Validated application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret settings required to construct and probe the application."""

    model_config = SettingsConfigDict(
        env_prefix="LLM_GATEWAY_",
        extra="ignore",
        frozen=True,
    )

    app_name: str = Field(default="llm-gateway", min_length=1, max_length=128)
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    correlation_id_header: str = Field(
        default="X-Request-ID",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9-]+$",
    )


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated process configuration."""

    return Settings(_env_file=".env", _env_file_encoding="utf-8")
