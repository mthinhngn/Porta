from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
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
    OpenAIResponsesProvider,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTokenUsage,
    ProviderUnavailableError,
)
from llm_gateway.services import GenerationService

AUTHORIZATION_HEADER = {"Authorization": "Bearer test-gateway-key"}
PRIVATE_PROMPT = "phase1-private-prompt-sentinel"
PRIVATE_OUTPUT = "phase1-private-output-sentinel"
PRIVATE_SECRET = "sk-phase1-private-secret-sentinel"


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
        raise AssertionError("invalid requests must not reach the provider")


class FailingCompleteLedger(SqlAlchemyGatewayLedger):
    def complete_generation(self, **kwargs: object) -> object:
        raise RuntimeError(f"disk full; prompt={PRIVATE_PROMPT}; secret={PRIVATE_SECRET}")


class AmbiguousCompleteLedger(SqlAlchemyGatewayLedger):
    def complete_generation(self, **kwargs: object) -> object:
        super().complete_generation(**kwargs)
        raise RuntimeError("connection lost after commit")


class FailingCompleteAndReconciliationLedger(FailingCompleteLedger):
    def reconcile_generation_success(self, **kwargs: object) -> object:
        raise RuntimeError(f"still offline; prompt={PRIVATE_PROMPT}; secret={PRIVATE_SECRET}")


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
            input_cost_per_million=Decimal("0.4000000000"),
            cached_input_cost_per_million=Decimal("0.1000000000"),
            output_cost_per_million=Decimal("1.6000000000"),
        ),
    )
    service.bootstrap()
    return service


def _client(
    tmp_path: Path,
    provider_registry: dict[str, GenerateProvider],
    *,
    ledger_type: type[SqlAlchemyGatewayLedger] = SqlAlchemyGatewayLedger,
    openai_api_key: str | None = None,
) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="INFO",
        openai_api_key=openai_api_key,
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": "00000000-0000-0000-0000-000000000201",
                "key": "test-gateway-key",
                "enabled": True,
            },
            {
                "api_key_id": "00000000-0000-0000-0000-000000000102",
                "actor_id": "00000000-0000-0000-0000-000000000202",
                "key": "disabled-gateway-key",
                "enabled": False,
            },
        ),
    )
    app = create_app(
        settings,
        generation_service=_service(
            tmp_path / "generate.sqlite3",
            provider_registry,
            ledger_type=ledger_type,
        ),
    )
    client = TestClient(app)
    client.headers.update(AUTHORIZATION_HEADER)
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


def _mock_openai_provider(
    response_body: dict[str, Any],
) -> tuple[OpenAIResponsesProvider, httpx.AsyncClient]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=response_body)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        OpenAIResponsesProvider(
            api_key=PRIVATE_SECRET,
            base_url="https://api.openai.com/v1",
            client=http_client,
        ),
        http_client,
    )


def test_generate_happy_path_returns_gate_fields_and_persists(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output=PRIVATE_OUTPUT,
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=5,
            ),
            provider_request_id="resp_123",
            cache_status="miss",
        )
    )

    with _client(
        tmp_path,
        {"openai": provider},
        openai_api_key=PRIVATE_SECRET,
    ) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": PRIVATE_PROMPT},
        )
        body = response.json()

        assert response.status_code == 200
        assert UUID(body["request_id"])
        assert body["output"] == PRIVATE_OUTPUT
        assert body["provider"] == "openai"
        assert body["model"] == "gateway-default"
        assert body["tokens"] == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
        assert body["cost"] == {"amount": "0.0000056000", "currency": "USD"}
        assert body["routing_reason"] == "configured_single_path"
        assert body["cache_status"] == "miss"
        assert body["latency_ms"] >= 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage = session.query(UsageRecord).one()

    assert request.request_payload_redacted is None
    assert request.error_message is None
    assert attempt.error_message is None
    assert usage.total_tokens == 5
    assert usage.estimated_cost == Decimal("0.0000056000")
    persisted = _database_dump(tmp_path / "generate.sqlite3")
    assert PRIVATE_PROMPT not in persisted
    assert PRIVATE_OUTPUT not in persisted
    assert PRIVATE_SECRET not in persisted


def test_generate_validation_stays_openai_shaped(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=ProviderTokenUsage(
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                total_tokens=2,
            ),
        )
    )

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_generate_rejects_small_max_output_tokens_before_ledger_or_provider(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "hello",
                "max_output_tokens": 15,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert provider.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()


def test_generate_timeout_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": StubProvider(ProviderTimeoutError("timed out"))}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"
    assert response.json()["error"]["message"] == "Provider request timed out."

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()

    assert request.status == "failed"
    assert request.error_message == "Provider request timed out."
    assert attempt.status == "timed_out"
    assert attempt.error_message == "Provider request timed out."


def test_generate_authentication_failure_maps_to_gateway_error(tmp_path: Path) -> None:
    with _client(
        tmp_path,
        {"openai": StubProvider(ProviderAuthenticationError("bad auth"))},
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_authentication_error"
    assert response.json()["error"]["message"] == "Provider authentication failed."

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()

    assert request.status == "failed"
    assert request.error_message == "Provider authentication failed."
    assert attempt.status == "failed"
    assert attempt.error_message == "Provider authentication failed."


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


@pytest.mark.parametrize(
    "response_body",
    [
        pytest.param(
            {
                "id": "resp_malformed_usage",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": PRIVATE_OUTPUT}],
                    }
                ],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 99,
                },
                "provider_debug": PRIVATE_SECRET,
            },
            id="malformed-usage",
        ),
        pytest.param(
            {
                "id": "resp_refusal",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": PRIVATE_SECRET}],
                    }
                ],
            },
            id="refusal",
        ),
        pytest.param(
            {
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": {"reason": PRIVATE_SECRET},
                "output": [],
            },
            id="incomplete",
        ),
    ],
)
def test_generate_rejects_unusable_openai_response_at_api_boundary(
    tmp_path: Path,
    response_body: dict[str, Any],
) -> None:
    provider, http_client = _mock_openai_provider(response_body)
    try:
        with _client(tmp_path, {"openai": provider}) as client:
            response = client.post(
                "/v1/generate",
                json={"model": "gateway-default", "input": PRIVATE_PROMPT},
            )
    finally:
        asyncio.run(http_client.aclose())

    assert response.status_code == 502
    assert response.json()["error"] == {
        "message": "Provider request failed.",
        "type": "server_error",
        "param": None,
        "code": "provider_invalid_response",
    }

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert request.error_message == "Provider request failed."
    assert attempt.status == "failed"
    assert attempt.error_message == "Provider request failed."
    assert usage_count == 0
    persisted = _database_dump(tmp_path / "generate.sqlite3")
    assert PRIVATE_PROMPT not in persisted
    assert PRIVATE_OUTPUT not in persisted
    assert PRIVATE_SECRET not in persisted


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


def test_generate_completion_failure_reconciles_provider_success(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=5,
            ),
            provider_request_id="resp_123",
        )
    )

    with _client(
        tmp_path,
        {"openai": provider},
        ledger_type=FailingCompleteLedger,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 200
    assert response.json()["output"] == "hello world"
    assert PRIVATE_PROMPT not in response.text
    assert PRIVATE_SECRET not in response.text

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "succeeded"
    assert request.error_code is None
    assert request.error_message is None
    assert attempt.status == "succeeded"
    assert attempt.error_code is None
    assert attempt.error_message is None
    assert usage_count == 1
    persisted = _database_dump(tmp_path / "generate.sqlite3")
    assert PRIVATE_PROMPT not in persisted
    assert PRIVATE_OUTPUT not in persisted
    assert PRIVATE_SECRET not in persisted


def test_generate_ambiguous_commit_reuses_persisted_usage(tmp_path: Path) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=1,
                output_tokens=3,
                total_tokens=5,
            ),
            provider_request_id="resp_ambiguous",
        )
    )

    with _client(
        tmp_path,
        {"openai": provider},
        ledger_type=AmbiguousCompleteLedger,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 200
    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_records = session.query(UsageRecord).all()

    assert request.status == "succeeded"
    assert attempt.status == "succeeded"
    assert attempt.upstream_request_id == "resp_ambiguous"
    assert len(usage_records) == 1
    assert usage_records[0].cached_input_tokens == 1


def test_generate_unrecoverable_persistence_failure_stays_reconcilable(
    tmp_path: Path,
) -> None:
    provider = StubProvider(
        GenerateProviderResult(
            output="hello world",
            usage=ProviderTokenUsage(
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=5,
            ),
            provider_request_id="resp_unpersisted",
        )
    )

    with _client(
        tmp_path,
        {"openai": provider},
        ledger_type=FailingCompleteAndReconciliationLedger,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "gateway_persistence_error"
    assert PRIVATE_PROMPT not in response.text
    assert PRIVATE_SECRET not in response.text

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "in_progress"
    assert request.error_code is None
    assert attempt.status == "in_progress"
    assert attempt.error_code is None
    assert usage_count == 0
    persisted = _database_dump(tmp_path / "generate.sqlite3")
    assert PRIVATE_PROMPT not in persisted
    assert PRIVATE_OUTPUT not in persisted
    assert PRIVATE_SECRET not in persisted


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


def test_generate_requires_gateway_api_key_before_provider_or_ledger(tmp_path: Path) -> None:
    provider = RecordingProvider()

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": ""},
            json={"model": "gateway-default", "input": "hello"},
        )

    assert response.status_code == 401
    assert response.json()["error"] == {
        "message": "Authentication required.",
        "type": "invalid_request_error",
        "param": None,
        "code": "authentication_error",
    }
    assert provider.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()


def test_generate_rejects_disabled_gateway_api_key_before_provider_or_ledger(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer disabled-gateway-key"},
            json={"model": "gateway-default", "input": "hello"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == {
        "message": "API key is disabled.",
        "type": "invalid_request_error",
        "param": None,
        "code": "authentication_error",
    }
    assert provider.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()
