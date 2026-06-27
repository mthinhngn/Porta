"""Validated application configuration."""

import base64
import binascii
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ENV_FILES = (".env", ".env.local")


def normalize_runtime_database_url(value: str | None) -> str | None:
    """Require a synchronous PostgreSQL driver for the synchronous ledger."""

    if value is None:
        return None

    url = make_url(value)
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    if url.drivername == "postgresql+asyncpg":
        raise ValueError(
            "runtime database URL must use postgresql+psycopg://; "
            "postgresql+asyncpg:// is reserved for Alembic migrations"
        )
    if url.get_backend_name() == "postgresql" and url.drivername != "postgresql+psycopg":
        raise ValueError("runtime PostgreSQL database URL must use postgresql+psycopg://")
    return value


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
    redis_url: str | None = Field(default=None, min_length=1, max_length=2048)
    provider_timeout_seconds: float = Field(default=30.0, gt=0)
    gateway_quota_window_seconds: int = Field(default=60, ge=1)
    gateway_cache_ttl_seconds: int = Field(default=300, ge=1)
    gateway_cache_encryption_key: SecretStr | None = None
    gateway_guardrail_version: str = Field(default="phase2-v1", min_length=1, max_length=64)
    gateway_guardrail_test_block_token: str = Field(
        default="BLOCK_ME_PHASE2",
        min_length=1,
        max_length=128,
    )
    auto_routing_enabled: bool = False
    openai_api_key: str | None = Field(default=None, min_length=1)
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        min_length=1,
        max_length=2048,
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        min_length=1,
        max_length=2048,
    )
    generate_primary_provider_adapter: str = Field(
        default="openai_responses",
        min_length=1,
        max_length=128,
    )
    generate_gateway_model: str = Field(default="gateway-default", min_length=1, max_length=255)
    generate_openai_upstream_model: str = Field(
        default="gpt-4.1-mini",
        min_length=1,
        max_length=255,
    )
    generate_openai_currency: str = Field(default="USD", min_length=3, max_length=3)
    generate_openai_input_cost_per_million: Decimal = Field(
        default=Decimal("0.4000000000"),
        ge=0,
    )
    generate_openai_cached_input_cost_per_million: Decimal = Field(
        default=Decimal("0.1000000000"),
        ge=0,
    )
    generate_openai_output_cost_per_million: Decimal = Field(
        default=Decimal("1.6000000000"),
        ge=0,
    )
    generate_llama_enabled: bool = False
    generate_llama_adapter: str = Field(
        default="ollama_generate",
        min_length=1,
        max_length=128,
    )
    generate_llama_upstream_model: str = Field(
        default="llama3.2:3b",
        min_length=1,
        max_length=255,
    )
    generate_qwen_enabled: bool = False
    generate_qwen_adapter: str = Field(
        default="ollama_generate",
        min_length=1,
        max_length=128,
    )
    generate_qwen_upstream_model: str = Field(
        default="qwen2.5-coder:3b",
        min_length=1,
        max_length=255,
    )
    gateway_api_keys: tuple["GatewayApiKeyConfig", ...] = ()
    live_smoke_enabled: bool = False

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        return normalize_runtime_database_url(value)

    @field_validator("gateway_cache_encryption_key", mode="before")
    @classmethod
    def validate_cache_encryption_key(cls, value: object) -> object:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        try:
            decoded = base64.b64decode(
                raw_value.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise ValueError("cache encryption key must be URL-safe base64") from exc
        if len(decoded) != 32:
            raise ValueError("cache encryption key must decode to exactly 32 bytes")
        return value

    @model_validator(mode="after")
    def validate_gateway_api_key_uniqueness(self) -> "Settings":
        keys = [item.key for item in self.gateway_api_keys]
        api_key_ids = [item.api_key_id for item in self.gateway_api_keys]
        if len(keys) != len(set(keys)):
            raise ValueError("gateway API keys must be unique")
        if len(api_key_ids) != len(set(api_key_ids)):
            raise ValueError("gateway API key IDs must be unique")
        return self


class GatewayApiKeyConfig(BaseModel):
    api_key_id: UUID
    actor_id: UUID
    key: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    is_admin: bool = False
    request_quota_limit: int | None = Field(default=None, ge=1)
    allowed_providers: tuple[Literal["openai", "llama", "qwen"], ...] | None = None


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated process configuration."""

    return Settings(_env_file=ENV_FILES, _env_file_encoding="utf-8")
