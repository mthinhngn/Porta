from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.domain import GenerateRequest
from llm_gateway.main import create_app
from llm_gateway.persistence import (
    Base,
    GatewayRequest,
    ProviderAttempt,
    RouteBootstrap,
    SqlAlchemyGatewayLedger,
    UsageRecord,
)
from llm_gateway.providers import (
    GenerateProvider,
    GenerateProviderContext,
    GenerateProviderResult,
    ProviderTokenUsage,
)
from llm_gateway.services import GenerationService


class CountingProvider(GenerateProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "openai"

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        self.calls += 1
        assert request.model == "gateway-default"
        assert context.provider_name == "openai"
        return GenerateProviderResult(
            output="cached hello",
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=5,
            ),
            provider_request_id=f"resp_{self.calls}",
            cache_status="miss",
        )


class StubCacheRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, name: str) -> object:
        return self.values.get(name)

    async def set(self, name: str, value: object, ex: int | None = None) -> object:
        assert isinstance(value, str)
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        raise AssertionError("quota eval should not be called in cache tests")

    async def aclose(self) -> None:
        return None


def _service(
    database_path: Path,
    provider_registry: dict[str, GenerateProvider],
) -> GenerationService:
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)
    service = GenerationService(
        provider_registry=provider_registry,
        ledger=ledger,
        timeout_seconds=5.0,
        provider_order=["openai"],
        bootstraps=(
            RouteBootstrap(
                provider_name="openai",
                provider_adapter="openai_responses",
                gateway_model="gateway-default",
                upstream_model="gpt-4.1-mini",
                currency="USD",
                input_cost_per_million=Decimal("0.4000000000"),
                cached_input_cost_per_million=Decimal("0.1000000000"),
                output_cost_per_million=Decimal("1.6000000000"),
            ),
        ),
    )
    service.bootstrap()
    return service


def _client(
    tmp_path: Path,
    redis_client: StubCacheRedisClient,
    provider: CountingProvider,
) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="INFO",
        redis_url="redis://example.test:6379/0",
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": "00000000-0000-0000-0000-000000000201",
                "key": "test-gateway-key-a",
                "enabled": True,
            },
            {
                "api_key_id": "00000000-0000-0000-0000-000000000102",
                "actor_id": "00000000-0000-0000-0000-000000000202",
                "key": "test-gateway-key-b",
                "enabled": True,
            },
        ),
    )
    app = create_app(
        settings,
        generation_service=_service(tmp_path / "cache.sqlite3", {"openai": provider}),
        redis_client=redis_client,
    )
    return TestClient(app)


def test_generate_second_same_actor_request_hits_gateway_cache(tmp_path: Path) -> None:
    redis_client = StubCacheRedisClient()
    provider = CountingProvider()

    with _client(tmp_path, redis_client, provider) as client:
        first = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key-a"},
            json={"model": "gateway-default", "input": "Say hello"},
        )
        second = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key-a"},
            json={"model": "gateway-default", "input": "Say hello"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["served_from_cache"] is False
    assert second.json()["served_from_cache"] is True
    assert provider.calls == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'cache.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 1
    engine.dispose()


def test_generate_different_actor_does_not_reuse_cached_response(tmp_path: Path) -> None:
    redis_client = StubCacheRedisClient()
    provider = CountingProvider()

    with _client(tmp_path, redis_client, provider) as client:
        first = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key-a"},
            json={"model": "gateway-default", "input": "Say hello"},
        )
        second = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key-b"},
            json={"model": "gateway-default", "input": "Say hello"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["served_from_cache"] is False
    assert second.json()["served_from_cache"] is False
    assert provider.calls == 2
