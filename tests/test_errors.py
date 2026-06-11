from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from llm_gateway.core.config import Settings
from llm_gateway.core.errors import ApiError
from llm_gateway.main import create_app


class InputBody(BaseModel):
    count: int = Field(ge=1)


def test_validation_error_is_openai_shaped(settings: Settings) -> None:
    app = create_app(settings)

    @app.post("/validation-test")
    async def validation_test(body: InputBody) -> dict[str, bool]:
        assert body.count > 0
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/validation-test", json={"count": 0})

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "Request validation failed.",
            "type": "invalid_request_error",
            "param": "count",
            "code": "validation_error",
        }
    }


def test_api_error_is_openai_shaped(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/api-error-test")
    async def api_error_test() -> None:
        raise ApiError(
            message="Model is unavailable.",
            type="invalid_request_error",
            status_code=400,
            param="model",
            code="model_not_found",
        )

    with TestClient(app) as client:
        response = client.get("/api-error-test")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "Model is unavailable.",
            "type": "invalid_request_error",
            "param": "model",
            "code": "model_not_found",
        }
    }


def test_http_error_preserves_string_detail_and_headers(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/http-error-test")
    async def http_error_test() -> None:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded.",
            headers={"Retry-After": "30"},
        )

    with TestClient(app) as client:
        response = client.get("/http-error-test")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json() == {
        "error": {
            "message": "Rate limit exceeded.",
            "type": "invalid_request_error",
            "param": None,
            "code": "http_error",
        }
    }


def test_http_error_hides_non_string_detail(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/structured-http-error-test")
    async def structured_http_error_test() -> None:
        raise HTTPException(
            status_code=400,
            detail={"internal_context": "authorization=Bearer private-secret"},
        )

    with TestClient(app) as client:
        response = client.get("/structured-http-error-test")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "Request failed.",
            "type": "invalid_request_error",
            "param": None,
            "code": "http_error",
        }
    }
    assert "private-secret" not in response.text


def test_http_error_hides_sensitive_string_detail_and_unsafe_headers(
    settings: Settings,
) -> None:
    app = create_app(settings)

    @app.get("/sensitive-http-error-test")
    async def sensitive_http_error_test() -> None:
        raise HTTPException(
            status_code=401,
            detail="authorization=Bearer private-secret",
            headers={
                "Authorization": "Bearer private-secret",
                "Set-Cookie": "session=private-secret",
                "X-Internal-Secret": "private-secret",
                "Retry-After": "30",
            },
        )

    with TestClient(app) as client:
        response = client.get("/sensitive-http-error-test")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Request failed."
    assert response.headers["retry-after"] == "30"
    assert "authorization" not in response.headers
    assert "set-cookie" not in response.headers
    assert "x-internal-secret" not in response.headers
    assert "private-secret" not in response.text


def test_internal_error_hides_exception_details(settings: Settings) -> None:
    app: FastAPI = create_app(settings)

    @app.get("/internal-error-test")
    async def internal_error_test() -> None:
        raise RuntimeError("authorization=Bearer private-secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/internal-error-test")

    body = response.json()
    assert response.status_code == 500
    assert body["error"] == {
        "message": "An internal server error occurred.",
        "type": "server_error",
        "param": None,
        "code": "internal_error",
    }
    assert response.headers["X-Request-ID"]
    assert "private-secret" not in response.text
