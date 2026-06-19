from __future__ import annotations

import base64
from decimal import Decimal
from pathlib import Path
from threading import Lock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.core.metrics import (
    generate_metrics,
    http_method_label,
    route_label,
    sanitize_bounded_label,
    status_labels,
)
from llm_gateway.domain import GenerateRequest
from llm_gateway.main import create_app
from llm_gateway.persistence import Base, RouteBootstrap, SqlAlchemyGatewayLedger
from llm_gateway.providers import (
    GenerateProvider,
    GenerateProviderContext,
    GenerateProviderResult,
    ProviderTokenUsage,
    ProviderUnavailableError,
)
from llm_gateway.services import GenerationService

PRIVATE_PROMPT = "private prompt sentinel"
PRIVATE_OUTPUT = "private output sentinel"
PRIVATE_SECRET = "sk-private-secret-sentinel"
PRIVATE_TOKEN = "Bearer private-token-sentinel"
AUTHORIZATION = {"Authorization": "Bearer test-gateway-key"}


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
        self.calls += 1
        if not self._outcomes:
            raise AssertionError(f"unexpected call to provider {self._name}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class MetricsRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.quotas: dict[str, int] = {}
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
        with self._lock:
            if "llm-gateway:quota" in str(keys_and_args[0]):
                key = str(keys_and_args[0])
                limit = int(keys_and_args[1])
                current = self.quotas.get(key, 0)
                if current >= limit:
                    return 0
                self.quotas[key] = current + 1
                return self.quotas[key]
            if numkeys == 2:
                lock_key, cache_key, owner, payload, _ttl = keys_and_args
                if self.values.get(str(lock_key)) != str(owner):
                    return 0
                self.values[str(cache_key)] = str(payload)
                return 1
            key, owner, *rest = keys_and_args
            if self.values.get(str(key)) != str(owner):
                return 0
            if rest or 'redis.call("del"' not in script:
                return 1
            del self.values[str(key)]
            return 1

    async def aclose(self) -> None:
        return None


def _provider_success(output: str = "hello world") -> GenerateProviderResult:
    return GenerateProviderResult(
        output=output,
        usage=ProviderTokenUsage(
            input_tokens=2,
            cached_input_tokens=0,
            output_tokens=3,
            total_tokens=5,
        ),
        provider_request_id="resp_private_provider_request_id",
        cache_status="miss",
    )


def _route(provider_name: str, upstream_model: str, adapter: str) -> RouteBootstrap:
    input_cost = Decimal("0.4000000000") if provider_name == "openai" else Decimal("0")
    cached_input_cost = Decimal("0.1000000000") if provider_name == "openai" else Decimal("0")
    output_cost = Decimal("1.6000000000") if provider_name == "openai" else Decimal("0")
    return RouteBootstrap(
        provider_name=provider_name,
        provider_adapter=adapter,
        gateway_model="gateway-default",
        upstream_model=upstream_model,
        currency="USD",
        input_cost_per_million=input_cost,
        cached_input_cost_per_million=cached_input_cost,
        output_cost_per_million=output_cost,
    )


def _service(
    database_path: Path,
    providers: dict[str, GenerateProvider],
    *,
    provider_order: list[str] | None = None,
    bootstraps: tuple[RouteBootstrap, ...] | None = None,
) -> GenerationService:
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)
    service = GenerationService(
        provider_registry=providers,
        ledger=ledger,
        timeout_seconds=5.0,
        provider_order=provider_order or ["openai"],
        bootstraps=bootstraps or (_route("openai", "gpt-4.1-mini", "openai_responses"),),
    )
    service.bootstrap()
    return service


def _client(
    tmp_path: Path,
    providers: dict[str, GenerateProvider],
    *,
    redis_client: MetricsRedisClient | None = None,
    quota_limit: int | None = None,
    cache_enabled: bool = False,
    provider_order: list[str] | None = None,
    bootstraps: tuple[RouteBootstrap, ...] | None = None,
) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="INFO",
        redis_url="redis://example.test:6379/0" if redis_client is not None else None,
        gateway_cache_encryption_key=(
            base64.urlsafe_b64encode(b"m" * 32).decode() if cache_enabled else None
        ),
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": "00000000-0000-0000-0000-000000000201",
                "key": "test-gateway-key",
                "enabled": True,
                "request_quota_limit": quota_limit,
            },
        ),
    )
    app = create_app(
        settings,
        generation_service=_service(
            tmp_path / "metrics.sqlite3",
            providers,
            provider_order=provider_order,
            bootstraps=bootstraps,
        ),
        redis_client=redis_client,
    )
    client = TestClient(app)
    client.headers.update(AUTHORIZATION)
    return client


def test_metric_label_helpers_bound_values_and_reject_sensitive_content() -> None:
    assert sanitize_bounded_label("openai") == "openai"
    assert sanitize_bounded_label(PRIVATE_PROMPT) == "unknown"
    assert sanitize_bounded_label(PRIVATE_SECRET) == "unknown"
    assert sanitize_bounded_label("x" * 129) == "unknown"
    assert sanitize_bounded_label("GET", allowed={"GET", "POST"}) == "GET"
    assert sanitize_bounded_label("TRACE", allowed={"GET", "POST"}) == "unknown"

    assert http_method_label("post") == "POST"
    assert http_method_label("TRACE") == "unknown"
    assert route_label("/health/live") == "/health/live"
    assert route_label(f"/unknown/{PRIVATE_PROMPT}") == "unknown"
    assert route_label("/unknown?authorization=secret") == "unknown"
    assert status_labels(204) == ("204", "2xx")
    assert status_labels(777) == ("unknown", "unknown")


def test_metrics_endpoint_returns_prometheus_text() -> None:
    app = create_app(Settings(environment="test", log_level="INFO"))

    with TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "llm_gateway_http_requests_total" in response.text
    assert "llm_gateway_http_request_duration_seconds" in response.text
    assert 'route="/health/live"' in response.text


def test_metrics_do_not_expose_private_request_material() -> None:
    app = create_app(Settings(environment="test", log_level="INFO"))

    with TestClient(app) as client:
        client.get(
            f"/unknown/{PRIVATE_PROMPT}",
            headers={"Authorization": PRIVATE_TOKEN, "X-Output": PRIVATE_OUTPUT},
            params={"secret": PRIVATE_SECRET},
        )
        body = client.get("/metrics").text

    for private_value in (PRIVATE_PROMPT, PRIVATE_OUTPUT, PRIVATE_SECRET, PRIVATE_TOKEN):
        assert private_value not in body
    assert "private" not in body


def test_generate_metrics_returns_bytes_without_private_material() -> None:
    body = generate_metrics().decode("utf-8")

    for private_value in (PRIVATE_PROMPT, PRIVATE_OUTPUT, PRIVATE_SECRET, PRIVATE_TOKEN):
        assert private_value not in body


def test_generate_auth_failure_records_pipeline_metrics(tmp_path: Path) -> None:
    provider = SequenceProvider("openai", [_provider_success()])

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": ""},
            json={"model": "gateway-default", "input": PRIVATE_PROMPT},
        )
        body = client.get("/metrics").text

    assert response.status_code == 401
    assert provider.calls == 0
    assert "llm_gateway_auth_events_total" in body
    assert 'result="failure"' in body
    assert 'error_code="authentication_error"' in body
    assert "llm_gateway_generate_events_total" in body
    assert 'stage="auth"' in body


def test_generate_guardrail_block_records_metrics_without_private_content(tmp_path: Path) -> None:
    provider = SequenceProvider("openai", [_provider_success(PRIVATE_OUTPUT)])

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "please BLOCK_ME_PHASE2 now"},
        )
        body = client.get("/metrics").text

    assert response.status_code == 400
    assert provider.calls == 0
    assert "llm_gateway_guardrail_events_total" in body
    assert 'result="block"' in body
    assert 'error_code="blocked_test_content"' in body
    for private_value in (PRIVATE_PROMPT, PRIVATE_OUTPUT, PRIVATE_SECRET, PRIVATE_TOKEN):
        assert private_value not in body


def test_generate_quota_cache_success_and_hit_record_metrics(tmp_path: Path) -> None:
    redis_client = MetricsRedisClient()
    provider = SequenceProvider("openai", [_provider_success()])

    with _client(
        tmp_path,
        {"openai": provider},
        redis_client=redis_client,
        quota_limit=3,
        cache_enabled=True,
    ) as client:
        first = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "cacheable hello"},
        )
        second = client.post(
            "/v1/generate",
            json={"model": "gateway-default", "input": "cacheable hello"},
        )
        body = client.get("/metrics").text

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["served_from_cache"] is True
    assert provider.calls == 1
    assert "llm_gateway_quota_events_total" in body
    assert "llm_gateway_cache_events_total" in body
    assert 'cache_status="miss"' in body
    assert 'cache_status="hit"' in body
    assert 'cache_status="publish_success"' in body
    assert "llm_gateway_generate_duration_seconds" in body


def test_generate_quota_exceeded_records_metrics(tmp_path: Path) -> None:
    redis_client = MetricsRedisClient()
    provider = SequenceProvider("openai", [_provider_success()])

    with _client(
        tmp_path,
        {"openai": provider},
        redis_client=redis_client,
        quota_limit=1,
        cache_enabled=False,
    ) as client:
        first = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        second = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        body = client.get("/metrics").text

    assert first.status_code == 200
    assert second.status_code == 429
    assert provider.calls == 1
    assert "llm_gateway_quota_events_total" in body
    assert 'result="exceeded"' in body
    assert 'error_code="quota_exceeded"' in body


def test_generate_provider_failure_records_attempt_and_ledger_metrics(tmp_path: Path) -> None:
    provider = SequenceProvider(
        "openai",
        [
            ProviderUnavailableError("first outage"),
            ProviderUnavailableError("second outage"),
        ],
    )

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        body = client.get("/metrics").text

    assert response.status_code == 503
    assert provider.calls == 2
    assert "llm_gateway_provider_attempts_total" in body
    assert "llm_gateway_provider_attempt_duration_seconds" in body
    assert "llm_gateway_ledger_operation_duration_seconds" in body
    assert 'provider="openai"' in body
    assert 'attempt_status="failed"' in body
    assert 'error_code="provider_unavailable"' in body


def test_generate_retry_fallback_success_records_provider_metrics(tmp_path: Path) -> None:
    openai = SequenceProvider(
        "openai",
        [
            ProviderUnavailableError("first outage"),
            ProviderUnavailableError("second outage"),
        ],
    )
    llama = SequenceProvider("llama", [_provider_success("fallback hello")])
    bootstraps = (
        _route("openai", "gpt-4.1-mini", "openai_responses"),
        _route("llama", "llama3.2:3b", "ollama_generate"),
    )

    with _client(
        tmp_path,
        {"openai": openai, "llama": llama},
        provider_order=["openai", "llama"],
        bootstraps=bootstraps,
    ) as client:
        response = client.post("/v1/generate", json={"model": "gateway-default", "input": "hello"})
        body = client.get("/metrics").text

    assert response.status_code == 200
    assert response.json()["provider"] == "llama"
    assert openai.calls == 2
    assert llama.calls == 1
    assert "llm_gateway_provider_attempts_total" in body
    assert 'provider="llama"' in body
    assert 'attempt_status="succeeded"' in body
    assert 'stage="generate"' in body
    assert 'result="success"' in body


def test_generate_pipeline_metrics_do_not_expose_private_material(tmp_path: Path) -> None:
    provider = SequenceProvider("openai", [_provider_success(PRIVATE_OUTPUT)])

    with _client(tmp_path, {"openai": provider}) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": "Bearer test-gateway-key", "X-Secret": PRIVATE_SECRET},
            json={"model": "gateway-default", "input": PRIVATE_PROMPT},
        )
        body = client.get("/metrics").text

    assert response.status_code == 200
    for private_value in (
        PRIVATE_PROMPT,
        PRIVATE_OUTPUT,
        PRIVATE_SECRET,
        PRIVATE_TOKEN,
        "resp_private_provider_request_id",
        "00000000-0000-0000-0000-000000000201",
    ):
        assert private_value not in body
    assert "llm-gateway:cache:" not in body
