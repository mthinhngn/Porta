import base64
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_gateway.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_does_not_load_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_GATEWAY_ENVIRONMENT=production\n", encoding="utf-8")

    assert Settings().environment == "development"


def test_get_settings_loads_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_GATEWAY_ENVIRONMENT=staging\n", encoding="utf-8")

    assert get_settings().environment == "staging"


def test_environment_overrides_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_GATEWAY_LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.setenv("LLM_GATEWAY_LOG_LEVEL", "ERROR")

    assert get_settings().log_level == "ERROR"


def test_get_settings_caches_loaded_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_GATEWAY_APP_NAME", "first-name")
    first = get_settings()
    monkeypatch.setenv("LLM_GATEWAY_APP_NAME", "second-name")

    assert get_settings() is first
    assert get_settings().app_name == "first-name"

    get_settings.cache_clear()
    assert get_settings().app_name == "second-name"


def test_default_generation_pricing_matches_configured_phase_one_model() -> None:
    settings = Settings()

    assert settings.generate_openai_upstream_model == "gpt-4.1-mini"
    assert settings.generate_openai_input_cost_per_million == Decimal("0.4000000000")
    assert settings.generate_openai_cached_input_cost_per_million == Decimal("0.1000000000")
    assert settings.generate_openai_output_cost_per_million == Decimal("1.6000000000")


def test_cache_encryption_key_requires_32_base64_encoded_bytes() -> None:
    valid_key = base64.urlsafe_b64encode(b"k" * 32).decode()

    assert Settings(gateway_cache_encryption_key=valid_key).gateway_cache_encryption_key is not None

    with pytest.raises(ValidationError, match="exactly 32 bytes"):
        Settings(gateway_cache_encryption_key=base64.urlsafe_b64encode(b"short").decode())


def test_gateway_api_key_values_and_ids_must_be_unique() -> None:
    first = {
        "api_key_id": "00000000-0000-0000-0000-000000000101",
        "actor_id": "00000000-0000-0000-0000-000000000201",
        "key": "shared-key",
    }

    with pytest.raises(ValidationError, match="gateway API keys must be unique"):
        Settings(
            gateway_api_keys=(
                first,
                {
                    "api_key_id": "00000000-0000-0000-0000-000000000102",
                    "actor_id": "00000000-0000-0000-0000-000000000202",
                    "key": "shared-key",
                },
            )
        )

    with pytest.raises(ValidationError, match="gateway API key IDs must be unique"):
        Settings(
            gateway_api_keys=(
                first,
                {
                    "api_key_id": "00000000-0000-0000-0000-000000000101",
                    "actor_id": "00000000-0000-0000-0000-000000000202",
                    "key": "different-key",
                },
            )
        )
