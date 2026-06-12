from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.persistence import Base, GatewayRequest, ProviderAttempt, UsageRecord

pytestmark = pytest.mark.skipif(
    os.getenv("LLM_GATEWAY_LIVE_SMOKE") != "1",
    reason="set LLM_GATEWAY_LIVE_SMOKE=1 to run the live OpenAI smoke test",
)


def test_live_generate_smoke(tmp_path: Path) -> None:
    database_path = tmp_path / "live-smoke.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path}",
        openai_api_key=os.environ["OPENAI_API_KEY"],
        generate_gateway_model=os.getenv("LLM_GATEWAY_SMOKE_GATEWAY_MODEL", "gateway-default"),
        generate_upstream_model=os.getenv("LLM_GATEWAY_SMOKE_MODEL", "gpt-4.1-mini"),
        generate_input_cost_per_million=Decimal(
            os.getenv("LLM_GATEWAY_SMOKE_INPUT_COST_PER_MILLION", "0.15")
        ),
        generate_output_cost_per_million=Decimal(
            os.getenv("LLM_GATEWAY_SMOKE_OUTPUT_COST_PER_MILLION", "0.6")
        ),
        live_smoke_enabled=True,
    )
    app = create_app(settings, session_factory=sessions)

    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": settings.generate_gateway_model,
                "input": "Respond with exactly: smoke-ok",
                "max_output_tokens": 16,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"]
    assert body["output"]
    assert body["provider"] == settings.generate_provider_name
    assert body["model"] == settings.generate_gateway_model
    assert body["tokens"]["total_tokens"] >= 1
    assert body["cost"]["currency"] == settings.generate_currency
    assert body["routing_reason"] == "configured_single_path"
    assert body["cache_status"] in {"miss", "hit"}
    assert body["latency_ms"] >= 0

    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 1


def test_live_generate_auth_failure_probe(tmp_path: Path) -> None:
    database_path = tmp_path / "live-smoke-failure.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path}",
        openai_api_key="invalid-phase1-smoke-key",
        generate_gateway_model=os.getenv("LLM_GATEWAY_SMOKE_GATEWAY_MODEL", "gateway-default"),
        generate_upstream_model=os.getenv("LLM_GATEWAY_SMOKE_MODEL", "gpt-4.1-mini"),
        generate_input_cost_per_million=Decimal(
            os.getenv("LLM_GATEWAY_SMOKE_INPUT_COST_PER_MILLION", "0.15")
        ),
        generate_output_cost_per_million=Decimal(
            os.getenv("LLM_GATEWAY_SMOKE_OUTPUT_COST_PER_MILLION", "0.6")
        ),
        live_smoke_enabled=True,
    )
    app = create_app(settings, session_factory=sessions)

    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            json={
                "model": settings.generate_gateway_model,
                "input": "Respond with exactly: smoke-auth-failure",
                "max_output_tokens": 16,
            },
        )

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "provider_authentication_error"
    assert body["error"]["type"] == "server_error"

    with sessions() as session:
        request = session.query(GatewayRequest).one()
        attempt = session.query(ProviderAttempt).one()
        usage_count = session.query(UsageRecord).count()

    assert request.status == "failed"
    assert request.error_code == "provider_authentication_error"
    assert attempt.status == "failed"
    assert attempt.error_code == "provider_authentication_error"
    assert usage_count == 0
