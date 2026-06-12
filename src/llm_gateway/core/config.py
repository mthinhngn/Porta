"""Validated application configuration."""

from decimal import Decimal
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
    database_url: str | None = Field(default=None, min_length=1, max_length=2048)
    provider_timeout_seconds: float = Field(default=30.0, gt=0)
    openai_api_key: str | None = Field(default=None, min_length=1)
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        min_length=1,
        max_length=2048,
    )
    generate_provider_name: str = Field(default="openai", min_length=1, max_length=128)
    generate_provider_adapter: str = Field(
        default="openai_responses",
        min_length=1,
        max_length=128,
    )
    generate_gateway_model: str = Field(default="gateway-default", min_length=1, max_length=255)
    generate_upstream_model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=255)
    generate_currency: str = Field(default="USD", min_length=3, max_length=3)
    generate_input_cost_per_million: Decimal = Field(default=Decimal("0.4000000000"), ge=0)
    generate_cached_input_cost_per_million: Decimal = Field(
        default=Decimal("0.1000000000"),
        ge=0,
    )
    generate_output_cost_per_million: Decimal = Field(default=Decimal("1.6000000000"), ge=0)
    live_smoke_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated process configuration."""

    return Settings(_env_file=".env", _env_file_encoding="utf-8")
