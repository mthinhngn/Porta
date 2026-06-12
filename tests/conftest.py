from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from llm_gateway.core.config import GatewayApiKeyConfig, Settings
from llm_gateway.main import create_app

TEST_API_KEY = "test-gateway-key"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        log_level="INFO",
        gateway_api_keys=(
            GatewayApiKeyConfig(
                api_key_id=UUID("00000000-0000-0000-0000-000000000101"),
                actor_id=UUID("00000000-0000-0000-0000-000000000201"),
                key=TEST_API_KEY,
                enabled=True,
            ),
        ),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
