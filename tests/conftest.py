from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_level="INFO")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
