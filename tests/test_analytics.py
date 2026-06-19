from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.persistence import (
    Base,
    GatewayRequest,
    Model,
    Provider,
    ProviderAttempt,
    UsageRecord,
)

ADMIN_KEY = "admin-gateway-key"
USER_KEY = "user-gateway-key"
PRIVATE_PROMPT = "private prompt sentinel"
PRIVATE_OUTPUT = "private output sentinel"
PRIVATE_SECRET = "sk-private-secret-sentinel"
PRIVATE_PROVIDER_REQUEST_ID = "resp_private_provider_request_id"
RAW_ACTOR_ID = "00000000-0000-0000-0000-000000000201"


def _client(tmp_path: Path) -> tuple[TestClient, sessionmaker]:
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        environment="test",
        log_level="INFO",
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": RAW_ACTOR_ID,
                "key": ADMIN_KEY,
                "enabled": True,
                "is_admin": True,
            },
            {
                "api_key_id": "00000000-0000-0000-0000-000000000102",
                "actor_id": "00000000-0000-0000-0000-000000000202",
                "key": USER_KEY,
                "enabled": True,
            },
        ),
    )
    return TestClient(create_app(settings, session_factory=sessions)), sessions


def _seed_analytics_rows(sessions: sessionmaker) -> None:
    now = datetime.now(UTC)
    with sessions.begin() as session:
        openai = Provider(name="openai", adapter="openai_responses")
        llama = Provider(name="llama", adapter="ollama_generate")
        session.add_all([openai, llama])
        session.flush()

        openai_model = Model(
            provider_id=openai.id,
            gateway_name="gateway-default",
            upstream_name="gpt-4.1-mini",
        )
        llama_model = Model(
            provider_id=llama.id,
            gateway_name="gateway-default",
            upstream_name="llama3.2:3b",
        )
        session.add_all([openai_model, llama_model])
        session.flush()

        success = GatewayRequest(
            correlation_id="analytics-correlation-success",
            status="succeeded",
            requested_model="gateway-default",
            started_at=now,
            completed_at=now,
        )
        failed = GatewayRequest(
            correlation_id="analytics-correlation-failed",
            status="failed",
            requested_model="gateway-default",
            error_code="provider_unavailable",
            error_message="Provider is unavailable.",
            started_at=now,
            completed_at=now,
        )
        anomaly = GatewayRequest(
            correlation_id="analytics-correlation-anomaly",
            status="succeeded",
            requested_model="gateway-default",
            started_at=now,
            completed_at=now,
        )
        session.add_all([success, failed, anomaly])
        session.flush()

        successful_attempt = ProviderAttempt(
            gateway_request_id=success.id,
            provider_id=openai.id,
            model_id=openai_model.id,
            attempt_number=1,
            status="succeeded",
            upstream_request_id=PRIVATE_PROVIDER_REQUEST_ID,
            latency_ms=42,
            started_at=now,
            completed_at=now,
        )
        failed_attempt = ProviderAttempt(
            gateway_request_id=failed.id,
            provider_id=llama.id,
            model_id=llama_model.id,
            attempt_number=1,
            status="failed",
            error_code="provider_unavailable",
            error_message="Provider is unavailable.",
            latency_ms=12,
            started_at=now,
            completed_at=now,
        )
        session.add_all([successful_attempt, failed_attempt])
        session.flush()

        session.add_all(
            [
                UsageRecord(
                    gateway_request_id=success.id,
                    provider_attempt_id=successful_attempt.id,
                    prompt_tokens=10,
                    cached_input_tokens=2,
                    completion_tokens=5,
                    total_tokens=15,
                    estimated_cost=Decimal("0.0000120000"),
                    currency="USD",
                ),
                UsageRecord(
                    gateway_request_id=failed.id,
                    provider_attempt_id=failed_attempt.id,
                    prompt_tokens=3,
                    cached_input_tokens=0,
                    completion_tokens=1,
                    total_tokens=4,
                    estimated_cost=Decimal("0"),
                    currency="USD",
                ),
            ]
        )


def test_usage_summary_requires_authentication(tmp_path: Path) -> None:
    client, _sessions = _client(tmp_path)

    with client:
        response = client.get("/v1/analytics/usage/summary")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


def test_usage_summary_rejects_non_admin_key(tmp_path: Path) -> None:
    client, _sessions = _client(tmp_path)

    with client:
        response = client.get(
            "/v1/analytics/usage/summary",
            headers={"Authorization": f"Bearer {USER_KEY}"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "analytics_access_denied"


def test_usage_summary_returns_empty_aggregate_for_empty_ledger(tmp_path: Path) -> None:
    client, _sessions = _client(tmp_path)

    with client:
        response = client.get(
            "/v1/analytics/usage/summary",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "request_statuses": [],
        "provider_model_attempts": [],
        "usage": {
            "usage_records": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
        "costs": [],
        "reconciliation": {
            "succeeded_requests_without_usage": 0,
            "usage_rows_without_succeeded_attempt": 0,
            "duplicate_charge_violations": 0,
        },
    }


def test_usage_summary_returns_privacy_safe_aggregates(tmp_path: Path) -> None:
    client, sessions = _client(tmp_path)
    _seed_analytics_rows(sessions)

    with client:
        response = client.get(
            "/v1/analytics/usage/summary",
            headers={"Authorization": f"Bearer {ADMIN_KEY}", "X-Secret": PRIVATE_SECRET},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["request_statuses"] == [
        {"status": "failed", "count": 1},
        {"status": "succeeded", "count": 2},
    ]
    assert body["provider_model_attempts"] == [
        {
            "provider": "llama",
            "model": "gateway-default",
            "status": "failed",
            "count": 1,
        },
        {
            "provider": "openai",
            "model": "gateway-default",
            "status": "succeeded",
            "count": 1,
        },
    ]
    assert body["usage"] == {
        "usage_records": 2,
        "input_tokens": 13,
        "cached_input_tokens": 2,
        "output_tokens": 6,
        "total_tokens": 19,
    }
    assert body["costs"] == [{"currency": "USD", "amount": "0.0000120000"}]
    assert body["reconciliation"] == {
        "succeeded_requests_without_usage": 1,
        "usage_rows_without_succeeded_attempt": 1,
        "duplicate_charge_violations": 0,
    }

    response_text = response.text
    for private_value in (
        PRIVATE_PROMPT,
        PRIVATE_OUTPUT,
        PRIVATE_SECRET,
        PRIVATE_PROVIDER_REQUEST_ID,
        RAW_ACTOR_ID,
    ):
        assert private_value not in response_text
    assert "correlation" not in response_text


def test_usage_summary_filters_by_provider_model_and_status(tmp_path: Path) -> None:
    client, sessions = _client(tmp_path)
    _seed_analytics_rows(sessions)

    with client:
        response = client.get(
            "/v1/analytics/usage/summary",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            params={"provider": "openai", "model": "gateway-default", "status": "succeeded"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["request_statuses"] == [{"status": "succeeded", "count": 1}]
    assert body["provider_model_attempts"] == [
        {
            "provider": "openai",
            "model": "gateway-default",
            "status": "succeeded",
            "count": 1,
        }
    ]
    assert body["usage"]["usage_records"] == 1
    assert body["usage"]["total_tokens"] == 15
    assert body["reconciliation"]["usage_rows_without_succeeded_attempt"] == 0


def test_usage_summary_reports_unconfigured_service(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        log_level="INFO",
        gateway_api_keys=(
            {
                "api_key_id": "00000000-0000-0000-0000-000000000101",
                "actor_id": RAW_ACTOR_ID,
                "key": ADMIN_KEY,
                "enabled": True,
                "is_admin": True,
            },
        ),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/analytics/usage/summary",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
