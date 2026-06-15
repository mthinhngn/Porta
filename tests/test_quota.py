from __future__ import annotations

import asyncio
import base64
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.core.quota import QUOTA_INCREMENT_SCRIPT, RedisQuotaEnforcer, quota_key
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

AUTHORIZATION_HEADER = {"Authorization": "Bearer test-gateway-key"}


class StubProvider(GenerateProvider):
    def __init__(self, result: GenerateProviderResult | Exception) -> None:
        self._result = result

    @property
    def name(self) -> str:
        return "openai"

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        assert request.model == "gateway-default"
        assert context.provider_name == "openai"
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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


class StubQuotaRedisClient:
    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._cache: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.get_calls = 0

    async def ping(self) -> bool:
        return True

    async def get(self, name: str) -> object:
        self.get_calls += 1
        return self._cache.get(name)

    async def delete(self, *names: str) -> int:
        deleted = sum(name in self._cache for name in names)
        for name in names:
            self._cache.pop(name, None)
        return deleted

    async def set(
        self,
        name: str,
        value: object,
        ex: int | None = None,
        nx: bool = False,
    ) -> object:
        assert isinstance(value, str)
        if nx and name in self._cache:
            return False
        self._cache[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        if script != QUOTA_INCREMENT_SCRIPT:
            if numkeys == 2:
                lock_key = str(keys_and_args[0])
                cache_key = str(keys_and_args[1])
                owner = str(keys_and_args[2])
                payload = str(keys_and_args[3])
                if self._cache.get(lock_key) != owner:
                    return 0
                self._cache[cache_key] = payload
                return 1
            assert numkeys == 1
            key = str(keys_and_args[0])
            owner = str(keys_and_args[1])
            if self._cache.get(key) != owner:
                return 0
            if 'redis.call("del"' in script:
                del self._cache[key]
            return 1
        assert numkeys == 1
        key = str(keys_and_args[0])
        limit = int(keys_and_args[1])
        async with self._lock:
            current = self._values.get(key, 0)
            if current >= limit:
                return 0
            updated = current + 1
            self._values[key] = updated
            if updated > limit:
                return 0
            return updated

    async def aclose(self) -> None:
        return None

    def value_for(self, key: str) -> int | None:
        return self._values.get(key)


class FailingQuotaRedisClient(StubQuotaRedisClient):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        raise RuntimeError("redis down")


def _quota_client(
    tmp_path: Path,
    redis_client: StubQuotaRedisClient,
    *,
    request_quota_limit: int,
    cache_enabled: bool = False,
) -> TestClient:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=5,
            ),
        )
    )
    settings = Settings(
        environment="test",
        log_level="INFO",
        redis_url="redis://example.test:6379/0",
        gateway_cache_encryption_key=(
            base64.urlsafe_b64encode(b"q" * 32).decode() if cache_enabled else None
        ),
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": "00000000-0000-0000-0000-000000000201",
                "key": "test-gateway-key",
                "enabled": True,
                "request_quota_limit": request_quota_limit,
            },
        ),
    )
    app = create_app(
        settings,
        generation_service=_service(tmp_path / "quota.sqlite3", {"openai": provider}),
        redis_client=redis_client,
    )
    client = TestClient(app)
    client.headers.update(AUTHORIZATION_HEADER)
    return client


def test_generate_quota_rejects_over_limit_before_provider_or_usage(tmp_path: Path) -> None:
    redis_client = StubQuotaRedisClient()

    with _quota_client(
        tmp_path,
        redis_client,
        request_quota_limit=1,
        cache_enabled=True,
    ) as client:
        first = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        second = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == {
        "message": "Quota exceeded.",
        "type": "server_error",
        "param": None,
        "code": "quota_exceeded",
    }
    assert redis_client.get_calls == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'quota.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 1
    engine.dispose()


def test_generate_quota_returns_service_unavailable_when_redis_eval_fails(
    tmp_path: Path,
) -> None:
    redis_client = FailingQuotaRedisClient()

    with _quota_client(tmp_path, redis_client, request_quota_limit=1) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"] == {
        "message": "Quota service is unavailable.",
        "type": "server_error",
        "param": None,
        "code": "service_unavailable",
    }


async def _run_enforcer_concurrently(
    enforcer: RedisQuotaEnforcer,
    *,
    actor_id: UUID,
    request_limit: int,
    window_seconds: int,
) -> list[str]:
    from llm_gateway.core.errors import ApiError
    from llm_gateway.core.quota import QuotaPolicy

    async def attempt() -> str:
        try:
            await enforcer.enforce(
                QuotaPolicy(
                    actor_id=actor_id,
                    request_limit=request_limit,
                    window_seconds=window_seconds,
                )
            )
        except ApiError as exc:
            return str(exc.status_code)
        return "200"

    return list(await asyncio.gather(attempt(), attempt()))


def test_quota_enforcer_allows_only_one_winner_for_last_slot() -> None:
    redis_client = StubQuotaRedisClient()
    actor_id = UUID("00000000-0000-0000-0000-000000000201")
    redis_client._values[quota_key(actor_id)] = 1
    enforcer = RedisQuotaEnforcer(redis_client)

    results = asyncio.run(
        _run_enforcer_concurrently(
            enforcer,
            actor_id=actor_id,
            request_limit=2,
            window_seconds=60,
        )
    )

    assert sorted(results) == ["200", "429"]
    assert redis_client.value_for(quota_key(actor_id)) == 2
