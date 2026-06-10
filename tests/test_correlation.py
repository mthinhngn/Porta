import re

from fastapi.testclient import TestClient


def test_valid_correlation_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "client.request-123"})

    assert response.headers["X-Request-ID"] == "client.request-123"


def test_invalid_correlation_id_is_replaced(client: TestClient) -> None:
    response = client.get(
        "/health/live",
        headers={"X-Request-ID": "authorization Bearer private-secret"},
    )

    correlation_id = response.headers["X-Request-ID"]
    assert correlation_id != "authorization Bearer private-secret"
    assert re.fullmatch(r"[0-9a-f]{32}", correlation_id)
