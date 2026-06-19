from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm_gateway.core.config import ENV_FILES, Settings
from llm_gateway.main import create_app
from llm_gateway.persistence import Base, GatewayRequest, ProviderAttempt, UsageRecord

LIVE_COST_LIMIT = Decimal("0.01")
SMOKE_MODEL = "gpt-4.1-mini"
SMOKE_PROMPT = "Respond with exactly: smoke-ok"
SMOKE_MAX_OUTPUT_TOKENS = 16
SMOKE_INPUT_TOKEN_BUDGET = 256
SMOKE_INPUT_COST_PER_MILLION = Decimal("0.4000000000")
SMOKE_CACHED_INPUT_COST_PER_MILLION = Decimal("0.1000000000")
SMOKE_OUTPUT_COST_PER_MILLION = Decimal("1.6000000000")
TOKENS_PER_MILLION = Decimal("1000000")
SMOKE_GATEWAY_KEY = "phase2-live-smoke-gateway-key"

pytestmark = pytest.mark.skipif(
    os.getenv("LLM_GATEWAY_LIVE_SMOKE") != "1",
    reason="set LLM_GATEWAY_LIVE_SMOKE=1 to run the live OpenAI smoke test",
)


def _live_settings(*, database_url: str, api_key: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        openai_api_key=api_key,
        generate_gateway_model=os.getenv(
            "LLM_GATEWAY_SMOKE_GATEWAY_MODEL",
            "gateway-default",
        ),
        generate_openai_upstream_model=SMOKE_MODEL,
        generate_openai_input_cost_per_million=SMOKE_INPUT_COST_PER_MILLION,
        generate_openai_cached_input_cost_per_million=SMOKE_CACHED_INPUT_COST_PER_MILLION,
        generate_openai_output_cost_per_million=SMOKE_OUTPUT_COST_PER_MILLION,
        gateway_api_keys=(
            {
                "api_key_id": UUID("00000000-0000-0000-0000-000000000901"),
                "actor_id": UUID("00000000-0000-0000-0000-000000000902"),
                "key": SMOKE_GATEWAY_KEY,
                "enabled": True,
            },
        ),
        live_smoke_enabled=True,
    )


def _smoke_cost_ceiling() -> Decimal:
    maximum_input_rate = max(
        SMOKE_INPUT_COST_PER_MILLION,
        SMOKE_CACHED_INPUT_COST_PER_MILLION,
    )
    return (
        Decimal(SMOKE_INPUT_TOKEN_BUDGET) * maximum_input_rate
        + Decimal(SMOKE_MAX_OUTPUT_TOKENS) * SMOKE_OUTPUT_COST_PER_MILLION
    ) / TOKENS_PER_MILLION


def test_live_generate_smoke(tmp_path: Path) -> None:
    assert _smoke_cost_ceiling() < LIVE_COST_LIMIT

    database_path = tmp_path / "live-smoke.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    dotenv_settings = Settings(_env_file=ENV_FILES, _env_file_encoding="utf-8")
    assert dotenv_settings.openai_api_key is not None, (
        "set LLM_GATEWAY_OPENAI_API_KEY in ignored .env.local"
    )
    settings = _live_settings(
        database_url=f"sqlite:///{database_path}",
        api_key=dotenv_settings.openai_api_key,
    )
    app = create_app(settings, session_factory=sessions)

    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {SMOKE_GATEWAY_KEY}"},
            json={
                "model": settings.generate_gateway_model,
                "input": SMOKE_PROMPT,
                "max_output_tokens": SMOKE_MAX_OUTPUT_TOKENS,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"]
    assert body["output"]
    assert body["provider"] == "openai"
    assert body["model"] == settings.generate_gateway_model
    assert body["tokens"]["total_tokens"] >= 1
    assert body["cost"]["currency"] == settings.generate_openai_currency
    assert body["routing_reason"] == "configured_single_path"
    assert body["cache_status"] in {"miss", "hit"}
    assert body["latency_ms"] >= 0
    assert body["tokens"]["input_tokens"] <= SMOKE_INPUT_TOKEN_BUDGET
    assert body["tokens"]["output_tokens"] <= SMOKE_MAX_OUTPUT_TOKENS
    estimated_cost = Decimal(body["cost"]["amount"])
    assert estimated_cost <= _smoke_cost_ceiling()
    assert estimated_cost < LIVE_COST_LIMIT
    print(f"live smoke estimated cost: USD {estimated_cost:.10f}")

    with sessions() as session:
        assert session.query(GatewayRequest).count() == 1
        assert session.query(ProviderAttempt).count() == 1
        assert session.query(UsageRecord).count() == 1


def test_live_generate_auth_failure_probe(tmp_path: Path) -> None:
    database_path = tmp_path / "live-smoke-failure.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    settings = _live_settings(
        database_url=f"sqlite:///{database_path}",
        api_key="invalid-phase1-smoke-key",
    )
    app = create_app(settings, session_factory=sessions)

    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {SMOKE_GATEWAY_KEY}"},
            json={
                "model": settings.generate_gateway_model,
                "input": "Respond with exactly: smoke-auth-failure",
                "max_output_tokens": SMOKE_MAX_OUTPUT_TOKENS,
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
