import re
from uuid import uuid4

from fastapi.testclient import TestClient


def test_valid_correlation_id_is_propagated(client: TestClient) -> None:
    correlation_id = uuid4().hex
    response = client.get("/health/live", headers={"X-Request-ID": correlation_id})

    assert response.headers["X-Request-ID"] == correlation_id


def test_arbitrary_opaque_correlation_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "client.request-123"})

    correlation_id = response.headers["X-Request-ID"]
    assert correlation_id != "client.request-123"
    assert re.fullmatch(r"[0-9a-f]{32}", correlation_id)


def test_invalid_correlation_id_is_replaced(client: TestClient) -> None:
    response = client.get(
        "/health/live",
        headers={"X-Request-ID": "authorization Bearer private-secret"},
    )

    correlation_id = response.headers["X-Request-ID"]
    assert correlation_id != "authorization Bearer private-secret"
    assert re.fullmatch(r"[0-9a-f]{32}", correlation_id)
