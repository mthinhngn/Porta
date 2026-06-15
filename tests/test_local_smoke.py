from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
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
    OllamaGenerateProvider,
    ProviderUnavailableError,
)
from llm_gateway.services import GenerationService

pytestmark = pytest.mark.skipif(
    os.getenv("LLM_GATEWAY_LOCAL_SMOKE") != "1",
    reason="set LLM_GATEWAY_LOCAL_SMOKE=1 to run free local Ollama smokes",
)

SMOKE_GATEWAY_KEY = "phase2-local-smoke-key"


class RetryableOpenAIFailure(GenerateProvider):
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
        raise ProviderUnavailableError("Provider is unavailable.")


def _route(provider: str, model: str) -> RouteBootstrap:
    return RouteBootstrap(
        provider_name=provider,
        provider_adapter="openai_responses" if provider == "openai" else "ollama_generate",
        gateway_model="gateway-default",
        upstream_model=model,
        currency="USD",
        input_cost_per_million=Decimal("0"),
        cached_input_cost_per_million=Decimal("0"),
        output_cost_per_million=Decimal("0"),
    )


@pytest.mark.parametrize(
    ("prompt", "winner", "expected_order"),
    [
        ("Reply with exactly: local-general-ok", "llama", ["openai", "openai", "llama"]),
        (
            "Write code that returns exactly: local-code-ok",
            "qwen",
            ["openai", "openai", "qwen"],
        ),
    ],
)
def test_real_ollama_fallback_smoke(
    tmp_path: Path,
    prompt: str,
    winner: str,
    expected_order: list[str],
) -> None:
    database_path = tmp_path / f"{winner}-local-smoke.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SqlAlchemyGatewayLedger(sessions)
    ollama_client = httpx.AsyncClient(base_url="http://127.0.0.1:11434")
    openai = RetryableOpenAIFailure()
    service = GenerationService(
        provider_registry={
            "openai": openai,
            "llama": OllamaGenerateProvider(client=ollama_client, name="llama"),
            "qwen": OllamaGenerateProvider(client=ollama_client, name="qwen"),
        },
        ledger=ledger,
        timeout_seconds=120,
        provider_order=["openai", "llama", "qwen"],
        bootstraps=(
            _route("openai", "gpt-4.1-mini"),
            _route("llama", "llama3.2:3b"),
            _route("qwen", "qwen2.5-coder:3b"),
        ),
    )
    settings = Settings(
        environment="test",
        gateway_api_keys=(
            {
                "api_key_id": UUID("00000000-0000-0000-0000-000000000911"),
                "actor_id": UUID("00000000-0000-0000-0000-000000000912"),
                "key": SMOKE_GATEWAY_KEY,
            },
        ),
    )
    app = create_app(settings, generation_service=service)

    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {SMOKE_GATEWAY_KEY}"},
            json={"model": "gateway-default", "input": prompt, "max_output_tokens": 16},
        )
        client.portal.call(ollama_client.aclose)

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == winner
    assert response.json()["attempt_count"] == 3
    assert Decimal(response.json()["cost"]["amount"]) == Decimal("0")
    assert openai.calls == 2

    with sessions() as session:
        attempts = session.query(ProviderAttempt).order_by(ProviderAttempt.attempt_number).all()
        providers = {item.id: item.name for item in session.query(Provider).all()}
        usage = session.query(UsageRecord).one()
        assert session.query(GatewayRequest).count() == 1
    assert [providers[item.provider_id] for item in attempts] == expected_order
    assert usage.provider_attempt_id == attempts[-1].id
    assert usage.estimated_cost == Decimal("0")
