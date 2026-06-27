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
from llm_gateway.core.errors import ApiError
from llm_gateway.domain import GenerateRequest
from llm_gateway.main import create_app
from llm_gateway.persistence import (
    Base,
    GatewayRequest,
    Provider,
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
    ProviderBadRequestError,
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


class SequenceProvider(GenerateProvider):
    def __init__(self, name: str, outcomes: list[GenerateProviderResult | Exception]) -> None:
        self._name = name
        self._outcomes = list(outcomes)
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        assert request.model == "gateway-default"
        assert context.provider_name == self._name
        self.calls += 1
        if not self._outcomes:
            raise AssertionError(f"unexpected extra call to {self._name}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SleepingProvider(GenerateProvider):
    def __init__(self, name: str, sleep_seconds: float) -> None:
        self._name = name
        self._sleep_seconds = sleep_seconds
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        self.calls += 1
        await asyncio.sleep(self._sleep_seconds)
        return GenerateProviderResult(
            output="late response",
            usage=ProviderTokenUsage(
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                total_tokens=2,
            ),
        )


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
    provider_order: list[str] | None = None,
    bootstraps: tuple[RouteBootstrap, ...] | None = None,
    timeout_seconds: float = 5.0,
    auto_routing_enabled: bool = True,
) -> GenerationService:
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = ledger_type(sessions)
    service = GenerationService(
        provider_registry=provider_registry,
        ledger=ledger,
        timeout_seconds=timeout_seconds,
        provider_order=provider_order or ["openai"],
        bootstraps=bootstraps
        or (
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
        auto_routing_enabled=auto_routing_enabled,
    )
    service.bootstrap()
    return service


def _client(
    tmp_path: Path,
    provider_registry: dict[str, GenerateProvider],
    *,
    ledger_type: type[SqlAlchemyGatewayLedger] = SqlAlchemyGatewayLedger,
    openai_api_key: str | None = None,
    provider_order: list[str] | None = None,
    bootstraps: tuple[RouteBootstrap, ...] | None = None,
    timeout_seconds: float = 5.0,
    allowed_providers: tuple[str, ...] | None = None,
    auto_routing_enabled: bool = True,
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
                "allowed_providers": allowed_providers,
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
            provider_order=provider_order,
            bootstraps=bootstraps,
            timeout_seconds=timeout_seconds,
            auto_routing_enabled=auto_routing_enabled,
        ),
    )
    client = TestClient(app)
    client.headers.update(AUTHORIZATION_HEADER)
    return client


def _phase4_route_bootstraps() -> tuple[RouteBootstrap, ...]:
    return (
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
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
        RouteBootstrap(
            provider_name="qwen",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="qwen2.5-coder:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )


def test_lease_loss_before_persistence_creates_no_usage_row(tmp_path: Path) -> None:
    provider = SequenceProvider(
        "openai",
        [
            GenerateProviderResult(
                output="must not be charged",
                usage=ProviderTokenUsage(
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        ],
    )
    database_path = tmp_path / "lease-fence.sqlite3"
    service = _service(database_path, {"openai": provider})
    checks = iter((True, False))

    async def lease_validator() -> bool:
        return next(checks)

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.generate(
                GenerateRequest(model="gateway-default", input="lease fence"),
                correlation_id="lease-fence",
                lease_validator=lease_validator,
            )
        )

    assert error.value.message == "Cache coordination was lost."
    assert provider.calls == 1
    engine = create_engine(f"sqlite:///{database_path}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).one().status == "failed"
        assert session.query(ProviderAttempt).one().status == "failed"
        assert session.query(UsageRecord).count() == 0
    engine.dispose()


def test_actor_provider_policy_stops_before_request_persistence(tmp_path: Path) -> None:
    provider = SequenceProvider(
        "openai",
        [
            GenerateProviderResult(
                output="must not run",
                usage=ProviderTokenUsage(
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        ],
    )

    with _client(
        tmp_path,
        {"openai": provider},
        allowed_providers=("qwen",),
    ) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "policy check"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_access_denied"
    assert provider.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()


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
        assert body["served_from_cache"] is False
        assert body["attempt_count"] == 1
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
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()

    assert request.status == "failed"
    assert request.error_message == "Provider request timed out."
    assert len(attempts) == 2
    assert {attempt.status for attempt in attempts} == {"timed_out"}
    assert {attempt.error_message for attempt in attempts} == {"Provider request timed out."}


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


def test_generate_non_retryable_error_does_not_retry_or_fallback(tmp_path: Path) -> None:
    openai = SequenceProvider("openai", [ProviderBadRequestError("bad request")])
    llama = SequenceProvider(
        "llama",
        [
            GenerateProviderResult(
                output="should not run",
                usage=ProviderTokenUsage(
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        ],
    )
    bootstraps = (
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
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama},
        provider_order=["openai", "llama"],
        bootstraps=bootstraps,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 400
    assert openai.calls == 1
    assert llama.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 0


def test_generate_general_task_retries_then_falls_back_to_llama(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [
            ProviderUnavailableError("first outage"),
            ProviderRateLimitError("still full"),
        ],
    )
    llama = SequenceProvider(
        "llama",
        [
            GenerateProviderResult(
                output="fallback hello",
                usage=ProviderTokenUsage(
                    input_tokens=3,
                    cached_input_tokens=0,
                    output_tokens=4,
                    total_tokens=7,
                ),
            )
        ],
    )
    bootstraps = (
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
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama},
        provider_order=["openai", "llama"],
        bootstraps=bootstraps,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "llama"
    assert body["attempt_count"] == 3
    assert body["routing_reason"] == "fallback_after_retry"
    assert openai.calls == 2
    assert llama.calls == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        providers = {provider.id: provider.name for provider in session.query(Provider).all()}
        usage_records = session.query(UsageRecord).all()

    assert [(providers[attempt.provider_id], attempt.attempt_number) for attempt in attempts] == [
        ("openai", 1),
        ("openai", 2),
        ("llama", 3),
    ]
    assert [attempt.status for attempt in attempts] == ["failed", "failed", "succeeded"]
    assert len(usage_records) == 1
    assert usage_records[0].provider_attempt_id == attempts[-1].id
    assert usage_records[0].estimated_cost == Decimal("0")


def test_generate_coding_task_retries_then_falls_back_to_qwen(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [ProviderUnavailableError("first outage"), ProviderRateLimitError("still full")],
    )
    qwen = SequenceProvider(
        "qwen",
        [
            GenerateProviderResult(
                output="def hello(): return 'hello'",
                usage=ProviderTokenUsage(
                    input_tokens=5,
                    cached_input_tokens=0,
                    output_tokens=6,
                    total_tokens=11,
                ),
            )
        ],
    )
    llama = SequenceProvider("llama", [ProviderUnavailableError("must not run")])
    bootstraps = (
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
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
        RouteBootstrap(
            provider_name="qwen",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="qwen2.5-coder:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama, "qwen": qwen},
        provider_order=["openai", "llama", "qwen"],
        bootstraps=bootstraps,
    ) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "Implement a Python function"},
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "qwen"
    assert response.json()["attempt_count"] == 3
    assert openai.calls == 2
    assert qwen.calls == 1
    assert llama.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        providers = {provider.id: provider.name for provider in session.query(Provider).all()}
        usage = session.query(UsageRecord).one()
    assert [(providers[item.provider_id], item.attempt_number) for item in attempts] == [
        ("openai", 1),
        ("openai", 2),
        ("qwen", 3),
    ]
    assert usage.provider_attempt_id == attempts[-1].id
    assert usage.estimated_cost == Decimal("0")


def test_generate_preferred_local_failure_uses_alternate_local_model(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [ProviderUnavailableError("first outage"), ProviderUnavailableError("second outage")],
    )
    qwen = SequenceProvider("qwen", [ProviderUnavailableError("qwen unavailable")])
    llama = SequenceProvider(
        "llama",
        [
            GenerateProviderResult(
                output="alternate local success",
                usage=ProviderTokenUsage(
                    input_tokens=3,
                    cached_input_tokens=0,
                    output_tokens=3,
                    total_tokens=6,
                ),
            )
        ],
    )
    local_route = lambda name, model: RouteBootstrap(  # noqa: E731
        provider_name=name,
        provider_adapter="ollama_generate",
        gateway_model="gateway-default",
        upstream_model=model,
        currency="USD",
        input_cost_per_million=Decimal("0"),
        cached_input_cost_per_million=Decimal("0"),
        output_cost_per_million=Decimal("0"),
    )
    bootstraps = (
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
        local_route("llama", "llama3.2:3b"),
        local_route("qwen", "qwen2.5-coder:3b"),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama, "qwen": qwen},
        provider_order=["openai", "llama", "qwen"],
        bootstraps=bootstraps,
    ) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "Debug this function"},
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "llama"
    assert response.json()["attempt_count"] == 4
    assert openai.calls == 2
    assert qwen.calls == 1
    assert llama.calls == 1


def test_generate_explicit_standard_tier_preserves_openai_first_order(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [
            GenerateProviderResult(
                output="standard success",
                usage=ProviderTokenUsage(
                    input_tokens=2,
                    cached_input_tokens=0,
                    output_tokens=2,
                    total_tokens=4,
                ),
            )
        ],
    )
    qwen = SequenceProvider("qwen", [ProviderUnavailableError("must not run")])
    llama = SequenceProvider("llama", [ProviderUnavailableError("must not run")])

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama, "qwen": qwen},
        provider_order=["openai", "llama", "qwen"],
        bootstraps=_phase4_route_bootstraps(),
    ) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "Implement a Python function",
                "tier": "standard",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "openai"
    assert response.json()["routing_reason"] == "configured_single_path"
    assert openai.calls == 1
    assert qwen.calls == 0
    assert llama.calls == 0


def test_generate_auto_tier_uses_evidence_approved_local_first_order(tmp_path: Path) -> None:
    openai = SequenceProvider("openai", [ProviderUnavailableError("must not run")])
    qwen = SequenceProvider(
        "qwen",
        [
            GenerateProviderResult(
                output="def slugify_title(text): return text",
                usage=ProviderTokenUsage(
                    input_tokens=5,
                    cached_input_tokens=0,
                    output_tokens=6,
                    total_tokens=11,
                ),
            )
        ],
    )
    llama = SequenceProvider("llama", [ProviderUnavailableError("must not run")])

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama, "qwen": qwen},
        provider_order=["openai", "llama", "qwen"],
        bootstraps=_phase4_route_bootstraps(),
    ) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "Implement a Python function",
                "tier": "auto",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "qwen"
    assert response.json()["routing_reason"] == "auto_routing_policy"
    assert response.json()["attempt_count"] == 1
    assert qwen.calls == 1
    assert openai.calls == 0
    assert llama.calls == 0


def test_generate_auto_tier_respects_provider_allowlist(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [
            GenerateProviderResult(
                output="allowlist success",
                usage=ProviderTokenUsage(
                    input_tokens=2,
                    cached_input_tokens=0,
                    output_tokens=2,
                    total_tokens=4,
                ),
            )
        ],
    )
    qwen = SequenceProvider("qwen", [ProviderUnavailableError("must not run")])
    llama = SequenceProvider("llama", [ProviderUnavailableError("must not run")])

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama, "qwen": qwen},
        provider_order=["openai", "llama", "qwen"],
        bootstraps=_phase4_route_bootstraps(),
        allowed_providers=("openai",),
    ) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "Implement a Python function",
                "tier": "auto",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "openai"
    assert response.json()["routing_reason"] == "auto_routing_policy"
    assert openai.calls == 1
    assert qwen.calls == 0
    assert llama.calls == 0


def test_generate_auto_tier_disabled_by_evidence_gate_stops_before_ledger(
    tmp_path: Path,
) -> None:
    qwen = SequenceProvider(
        "qwen",
        [
            GenerateProviderResult(
                output="must not run",
                usage=ProviderTokenUsage(
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        ],
    )

    with _client(
        tmp_path,
        {"qwen": qwen},
        provider_order=["qwen"],
        bootstraps=(_phase4_route_bootstraps()[2],),
        auto_routing_enabled=False,
    ) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "Implement a Python function",
                "tier": "auto",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auto_routing_unavailable"
    assert qwen.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert session.query(GatewayRequest).count() == 0
        assert session.query(ProviderAttempt).count() == 0
        assert session.query(UsageRecord).count() == 0
    engine.dispose()


def test_generate_rejects_invalid_tier_before_ledger_or_provider(tmp_path: Path) -> None:
    provider = RecordingProvider()

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "hello", "tier": "premium"},
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


def test_generate_auto_tier_fallback_preserves_single_usage_row(tmp_path: Path) -> None:
    qwen = SequenceProvider("qwen", [ProviderUnavailableError("qwen unavailable")])
    llama = SequenceProvider(
        "llama",
        [
            GenerateProviderResult(
                output="alternate local success",
                usage=ProviderTokenUsage(
                    input_tokens=3,
                    cached_input_tokens=0,
                    output_tokens=3,
                    total_tokens=6,
                ),
            )
        ],
    )

    with _client(
        tmp_path,
        {"qwen": qwen, "llama": llama},
        provider_order=["llama", "qwen"],
        bootstraps=_phase4_route_bootstraps()[1:],
    ) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": "gateway-default",
                "input": "Debug this function",
                "tier": "auto",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "llama"
    assert response.json()["attempt_count"] == 2
    assert qwen.calls == 1
    assert llama.calls == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        usage_records = session.query(UsageRecord).all()
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert len(usage_records) == 1
    assert usage_records[0].provider_attempt_id == attempts[-1].id
    engine.dispose()


def test_generate_deadline_exhaustion_stops_extra_attempts(tmp_path: Path) -> None:
    openai = SleepingProvider("openai", sleep_seconds=0.3)
    llama = SequenceProvider(
        "llama",
        [
            GenerateProviderResult(
                output="should not run",
                usage=ProviderTokenUsage(
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    total_tokens=2,
                ),
            )
        ],
    )
    bootstraps = (
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
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model="gateway-default",
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama},
        provider_order=["openai", "llama"],
        bootstraps=bootstraps,
        timeout_seconds=0.1,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"
    assert openai.calls == 1
    assert llama.calls == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        request = session.query(GatewayRequest).one()
        usage_count = session.query(UsageRecord).count()

    assert len(attempts) == 1
    assert attempts[0].status == "timed_out"
    assert request.status == "failed"
    assert request.error_code == "provider_timeout"
    assert usage_count == 0


def test_generate_unexpected_provider_exception_is_terminal(tmp_path: Path) -> None:
    with _client(tmp_path, {"openai": ExplodingProvider()}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"

    engine = create_engine(f"sqlite:///{tmp_path / 'generate.sqlite3'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert len(attempts) == 2
    assert {attempt.status for attempt in attempts} == {"failed"}
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

    assert response.status_code == 503
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
