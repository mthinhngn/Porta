import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app


def test_application_starts_with_valid_configuration(settings: Settings) -> None:
    app = create_app(settings)

    with TestClient(app) as client:
        assert app.state.settings is settings
        assert client.get("/openapi.json").status_code == 200


def test_settings_reject_invalid_environment() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "unsafe"})


def test_settings_do_not_require_database_or_provider_credentials() -> None:
    settings = Settings(environment="test")

    assert settings.environment == "test"
    assert not hasattr(settings, "database_url")
    assert not hasattr(settings, "provider_api_key")


def test_settings_reject_invalid_correlation_header() -> None:
    with pytest.raises(ValidationError):
        Settings(correlation_id_header="bad header")


def test_chat_completion_endpoint_is_not_implemented(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "private"}]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "invalid_request_error"
