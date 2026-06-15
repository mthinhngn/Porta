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

BLOCKED_PROMPT = "Please BLOCK_ME_PHASE2 immediately."


class RecordingProvider(GenerateProvider):
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
        return GenerateProviderResult(
            output="should not run",
            usage=ProviderTokenUsage(
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                total_tokens=2,
            ),
        )


class TrackingRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.eval_calls = 0

    async def ping(self) -> bool:
        return True

    async def get(self, name: str) -> object:
        self.get_calls += 1
        return self.values.get(name)

    async def set(self, name: str, value: object, ex: int | None = None) -> object:
        self.set_calls += 1
        assert isinstance(value, str)
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        self.eval_calls += 1
        return 1

    async def aclose(self) -> None:
        return None


def _service(database_path: Path, provider: GenerateProvider) -> GenerationService:
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)
    service = GenerationService(
        provider_registry={"openai": provider},
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
    redis_client: TrackingRedisClient,
    provider: RecordingProvider,
) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="INFO",
        redis_url="redis://example.test:6379/0",
        gateway_guardrail_test_block_token="BLOCK_ME_PHASE2",
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": "00000000-0000-0000-0000-000000000201",
                "key": "test-gateway-key",
                "enabled": True,
                "request_quota_limit": 5,
            },
        ),
    )
    app = create_app(
        settings,
        generation_service=_service(tmp_path / "guardrails.sqlite3", provider),
        redis_client=redis_client,
    )
    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer test-gateway-key"})
    return client


def _database_dump(database_path: Path) -> str:
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            rows = {
                table_name: [
                    dict(row)
                    for row in connection.exec_driver_sql(
                        f'SELECT * FROM "{table_name}"'
                    ).mappings()
                ]
                for table_name in sorted(Base.metadata.tables)
            }
    finally:
        engine.dispose()
    return repr(rows)


def test_generate_blocked_request_returns_sanitized_denial_before_quota_cache_or_provider(
    tmp_path: Path,
) -> None:
    redis_client = TrackingRedisClient()
    provider = RecordingProvider()

    with _client(tmp_path, redis_client, provider) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": BLOCKED_PROMPT},
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": "Request blocked by gateway guardrails.",
        "type": "invalid_request_error",
        "param": None,
        "code": "blocked_test_content",
    }
    assert BLOCKED_PROMPT not in response.text
    assert provider.calls == 0
    assert redis_client.eval_calls == 0
    assert redis_client.get_calls == 0
    assert redis_client.set_calls == 0
    assert redis_client.values == {}

    engine = create_engine(f"sqlite:///{tmp_path / 'guardrails.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()

    persisted = _database_dump(tmp_path / "guardrails.sqlite3")
    assert BLOCKED_PROMPT not in persisted


def test_generate_allowed_request_still_reaches_quota_cache_and_provider(tmp_path: Path) -> None:
    redis_client = TrackingRedisClient()
    provider = RecordingProvider()

    with _client(tmp_path, redis_client, provider) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "Say hello."},
        )

    assert response.status_code == 200
    assert response.json()["output"] == "should not run"
    assert provider.calls == 1
    assert redis_client.eval_calls == 1
    assert redis_client.get_calls == 1
    assert redis_client.set_calls == 1
