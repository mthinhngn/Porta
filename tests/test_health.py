from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_gateway.api.routes.health import router as health_router
from llm_gateway.core.errors import install_error_handlers


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_readiness_only_requires_loaded_configuration(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_returns_not_ready_when_configuration_is_missing() -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(health_router, prefix="/health")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "message": "Application configuration is unavailable.",
            "type": "server_error",
            "param": None,
            "code": "not_ready",
        }
    }


def test_readiness_returns_not_ready_when_configuration_is_invalid() -> None:
    app = FastAPI()
    app.state.settings = object()
    install_error_handlers(app)
    app.include_router(health_router, prefix="/health")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
