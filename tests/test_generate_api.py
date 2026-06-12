from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.domain import GenerateRequest, GenerateTokenUsage
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
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from llm_gateway.services import GenerationService


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


class ExplodingProvider(GenerateProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        raise RuntimeError("boom")


class FailingCompleteLedger(SqlAlchemyGatewayLedger):
    def complete_generation(self, **kwargs: object) -> object:
        raise RuntimeError("disk full")


def _service(
    database_path: Path,
    provider_registry: dict[str, GenerateProvider],
    *,
    ledger_type: type[SqlAlchemyGatewayLedger] = SqlAlchemyGatewayLedger,
) -> GenerationService:
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = ledger_type(sessions)
    service = GenerationService(
        provider_registry=provider_registry,
        ledger=ledger,
        timeout_seconds=5.0,
        bootstrap=RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model="gateway-default",
            upstream_model="gpt-4.1-mini",
            currency="USD",
            input_cost_per_million=Decimal("0.1500000000"),
            output_cost_per_million=Decimal("0.6000000000"),
        ),
    )
    service.bootstrap()
    return service


def _client(
    tmp_path: Path,
    provider_registry: dict[str, GenerateProvider],
    *,
    ledger_type: type[SqlAlchemyGatewayLedger] = SqlAlchemyGatewayLedger,
) -> TestClient:
    settings = Settings(environment="test", log_level="INFO")
    app = create_app(
        settings,
        generation_service=_service(
            tmp_path / "generate.sqlite3",
            provider_registry,
            ledger_type=ledger_type,
        ),
    )
    return TestClient(app)


def test_generate_happy_path_returns_gate_fields_and_persists(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            provider_request_id="resp_123",
            cache_status="miss",
        )
    )

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        body = response.json()

        assert response.status_code == 200
        assert UUID(body["request_id"])
        assert body["output"] == "hello world"
        assert body["provider"] == "openai"
        assert body["model"] == "gateway-default"
        assert body["tokens"] == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
        assert body["cost"] == {"amount": "0.0000021000", "currency": "USD"}
        assert body["routing_reason"] == "configured_single_path"
        assert body["cache_status"] == "miss"
        assert body["latency_ms"] >= 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        usage = session.query(UsageRecord).one()

    assert usage.total_tokens == 5
    assert usage.estimated_cost == Decimal("0.0000021000")


def test_generate_validation_stays_openai_shaped(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=GenerateTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
    )

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_generate_timeout_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": StubProvider(ProviderTimeoutError("timed out"))}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"


def test_generate_authentication_failure_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(
        tmp_path,
        {"openai": StubProvider(ProviderAuthenticationError("bad auth"))},
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_authentication_error"


def test_generate_rate_limit_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": StubProvider(ProviderRateLimitError("slow down"))}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "provider_rate_limit"


def test_generate_provider_unavailable_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": StubProvider(ProviderUnavailableError("offline"))}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_generate_malformed_provider_response_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(
        tmp_path,
        {"openai": StubProvider(ProviderResponseError("bad payload"))},
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_invalid_response"


def test_generate_unexpected_provider_exception_is_terminal(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": ExplodingProvider()}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert attempt.status == "failed"
    assert usage_count == 0


def test_generate_persistence_failure_reconciles_to_terminal_failure(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            provider_request_id="resp_123",
        )
    )

    with _client(
        tmp_path,
        {"openai": provider},
        ledger_type=FailingCompleteLedger,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "gateway_persistence_error"

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert request.error_code == "gateway_persistence_error"
    assert attempt.status == "failed"
    assert attempt.error_code == "gateway_persistence_error"
    assert usage_count == 0


def test_generate_missing_registered_provider_fails_terminally(tmp_path: Path) -> None:
    with _client(tmp_path, {}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "provider_not_configured"

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert request.error_code == "provider_not_configured"
    assert attempt.status == "failed"
    assert attempt.error_code == "provider_not_configured"
    assert usage_count == 0
