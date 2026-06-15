from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_gateway.api.routes.health import router as health_router
from llm_gateway.core.config import Settings
from llm_gateway.core.errors import install_error_handlers
from llm_gateway.main import create_app


class StubRedisClient:
    def __init__(self, *, ping_result: bool = True, raises: bool = False) -> None:
        self._ping_result = ping_result
        self._raises = raises
        self.closed = False

    async def ping(self) -> bool:
        if self._raises:
            raise RuntimeError("redis down")
        return self._ping_result

    async def get(self, name: str) -> object:
        raise AssertionError("cache get should not be called in health tests")

    async def set(self, name: str, value: object, ex: int | None = None) -> object:
        raise AssertionError("cache set should not be called in health tests")

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        raise AssertionError("quota eval should not be called in health tests")

    async def aclose(self) -> None:
        self.closed = True


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_readiness_returns_ready_when_redis_is_reachable() -> None:
    app = create_app(
        Settings(environment="test", log_level="INFO", redis_url="redis://example.test:6379/0"),
        redis_client=StubRedisClient(),
    )

    with TestClient(app) as client:
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


def test_readiness_returns_not_ready_when_redis_client_is_missing() -> None:
    app = create_app(Settings(environment="test", log_level="INFO"))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "message": "Redis is unavailable.",
            "type": "server_error",
            "param": None,
            "code": "not_ready",
        }
    }


def test_readiness_returns_not_ready_when_redis_ping_fails() -> None:
    app = create_app(
        Settings(environment="test", log_level="INFO", redis_url="redis://example.test:6379/0"),
        redis_client=StubRedisClient(raises=True),
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
