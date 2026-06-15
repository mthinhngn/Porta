from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Lock

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
    ProviderUnavailableError,
)
from llm_gateway.services import GenerationService


class CountingProvider(GenerateProvider):
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.calls = 0
        self.delay_seconds = delay_seconds

    @property
    def name(self) -> str:
        return "openai"

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
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
        self._lock = Lock()

    async def ping(self) -> bool:
        return True

    async def get(self, name: str) -> object:
        with self._lock:
            return self.values.get(name)

    async def delete(self, *names: str) -> int:
        with self._lock:
            deleted = sum(name in self.values for name in names)
            for name in names:
                self.values.pop(name, None)
            return deleted

    async def set(
        self,
        name: str,
        value: object,
        ex: int | None = None,
        nx: bool = False,
    ) -> object:
        assert isinstance(value, str)
        with self._lock:
            if nx and name in self.values:
                return False
            self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        assert numkeys == 1
        key, owner = keys_and_args
        assert isinstance(key, str)
        assert isinstance(owner, str)
        with self._lock:
            if self.values.get(key) != owner:
                return 0
            del self.values[key]
            return 1

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
        gateway_cache_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
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
    stored_values = list(redis_client.values.values())
    assert stored_values
    assert all("cached hello" not in value for value in stored_values)
    assert all("Say hello" not in value for value in stored_values)

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


def test_concurrent_identical_requests_share_one_provider_result(tmp_path: Path) -> None:
    redis_client = StubCacheRedisClient()
    provider = CountingProvider(delay_seconds=0.1)

    with _client(tmp_path, redis_client, provider) as client:

        def send() -> object:
            return client.post(
                "/v1/generate",
                headers={"Authorization": "Bearer test-gateway-key-a"},
                json={"model": "gateway-default", "input": "Concurrent hello"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: send(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["served_from_cache"] for response in responses) == [False, True]
    assert provider.calls == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'cache.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 1
    engine.dispose()


def test_provider_failure_writes_no_cache_value_or_usage(tmp_path: Path) -> None:
    redis_client = StubCacheRedisClient()
    provider = CountingProvider()

    async def fail(
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        provider.calls += 1
        raise ProviderUnavailableError("private provider failure")

    provider.generate = fail  # type: ignore[method-assign]

    with _client(tmp_path, redis_client, provider) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key-a"},
            json={"model": "gateway-default", "input": "Failure sentinel"},
        )

    assert response.status_code == 503
    assert provider.calls == 2
    assert redis_client.values == {}

    engine = create_engine(f"sqlite:///{tmp_path / 'cache.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 2
        assert session.query(UsageRecord).count() == 0
    engine.dispose()
