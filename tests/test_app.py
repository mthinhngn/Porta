import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app, run


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
    assert settings.database_url is None
    assert settings.openai_api_key is None


def test_settings_reject_invalid_correlation_header() -> None:
    with pytest.raises(ValidationError):
        Settings(correlation_id_header="bad header")


def test_runtime_postgresql_url_normalizes_to_sync_psycopg_driver() -> None:
    settings = Settings(database_url="postgresql://gateway:secret@db.example/llm_gateway")

    assert settings.database_url == ("postgresql+psycopg://gateway:secret@db.example/llm_gateway")
    engine = create_engine(settings.database_url)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        engine.dispose()


def test_runtime_rejects_asyncpg_url_before_application_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_engine_called = False

    def unexpected_create_engine(*args: object, **kwargs: object) -> None:
        nonlocal create_engine_called
        create_engine_called = True

    monkeypatch.setattr("llm_gateway.main.create_engine", unexpected_create_engine)

    with pytest.raises(ValidationError, match=r"postgresql\+psycopg"):
        Settings(database_url="postgresql+asyncpg://localhost/llm_gateway")

    assert create_engine_called is False


def test_safe_server_entrypoint_disables_uvicorn_access_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("llm_gateway.main.uvicorn.run", fake_run)

    run()

    assert captured == {
        "app": "llm_gateway.main:app",
        "access_log": False,
    }


def test_chat_completion_endpoint_is_not_implemented(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "private"}]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_generate_endpoint_requires_configured_service(client: TestClient) -> None:
    response = client.post(
        "/v1/generate",
        json={"model": "test", "input": "hello"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
