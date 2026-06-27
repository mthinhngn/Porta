"""ASGI application composition."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import cast

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from llm_gateway.api.router import api_router
from llm_gateway.core.auth import GatewayAuthMiddleware
from llm_gateway.core.config import Settings, get_settings
from llm_gateway.core.errors import install_error_handlers
from llm_gateway.core.logging import configure_logging
from llm_gateway.core.middleware import CorrelationIdMiddleware
from llm_gateway.core.ollama import build_ollama_client
from llm_gateway.core.redis import RedisClient, build_redis_client
from llm_gateway.persistence.ledger import RouteBootstrap, SqlAlchemyGatewayLedger
from llm_gateway.providers import GenerateProvider, OllamaGenerateProvider, OpenAIResponsesProvider
from llm_gateway.services.generation import GenerationService


def create_app(
    settings: Settings | None = None,
    *,
    generation_service: GenerationService | None = None,
    session_factory: sessionmaker[Session] | None = None,
    redis_client: RedisClient | None = None,
    ollama_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.generation_service = generation_service
        app.state.session_factory = session_factory
        app.state.redis_client = redis_client
        app.state.ollama_client = ollama_client
        app.state.owns_ollama_client = False
        if app.state.redis_client is None and app_settings.redis_url is not None:
            app.state.redis_client = build_redis_client(app_settings.redis_url)
        if app.state.ollama_client is None and (
            app_settings.generate_llama_enabled or app_settings.generate_qwen_enabled
        ):
            app.state.ollama_client = build_ollama_client(app_settings.ollama_base_url)
            app.state.owns_ollama_client = True
        if app.state.generation_service is None and session_factory is not None:
            app.state.generation_service = _build_generation_service(
                settings=app_settings,
                session_factory=session_factory,
                ollama_client=app.state.ollama_client,
            )
        elif app.state.generation_service is None and app_settings.database_url is not None:
            engine = create_engine(app_settings.database_url)
            app.state.engine = engine
            db_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            app.state.session_factory = db_session_factory
            app.state.generation_service = _build_generation_service(
                settings=app_settings,
                session_factory=db_session_factory,
                ollama_client=app.state.ollama_client,
            )
        service = getattr(app.state, "generation_service", None)
        if isinstance(service, GenerationService):
            service.bootstrap()
        yield
        teardown_redis = cast(RedisClient | None, getattr(app.state, "redis_client", None))
        if teardown_redis is not None:
            await teardown_redis.aclose()
        if app.state.owns_ollama_client and app.state.ollama_client is not None:
            await app.state.ollama_client.aclose()
        teardown_engine = cast(Engine | None, getattr(app.state, "engine", None))
        if teardown_engine is not None:
            teardown_engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.include_router(api_router)
    app.add_middleware(GatewayAuthMiddleware)
    app.add_middleware(
        CorrelationIdMiddleware,
        header_name=app_settings.correlation_id_header,
    )
    return app


def _build_generation_service(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    ollama_client: httpx.AsyncClient | None,
) -> GenerationService:
    provider_registry: dict[str, GenerateProvider] = {
        "openai": OpenAIResponsesProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            name="openai",
        )
    }
    bootstraps = [
        RouteBootstrap(
            provider_name="openai",
            provider_adapter=settings.generate_primary_provider_adapter,
            gateway_model=settings.generate_gateway_model,
            upstream_model=settings.generate_openai_upstream_model,
            currency=settings.generate_openai_currency,
            input_cost_per_million=settings.generate_openai_input_cost_per_million,
            cached_input_cost_per_million=settings.generate_openai_cached_input_cost_per_million,
            output_cost_per_million=settings.generate_openai_output_cost_per_million,
        )
    ]
    provider_order = ["openai"]
    if settings.generate_llama_enabled:
        if ollama_client is None:
            raise RuntimeError("Ollama client is required when local providers are enabled")
        provider_registry["llama"] = OllamaGenerateProvider(client=ollama_client, name="llama")
        bootstraps.append(
            RouteBootstrap(
                provider_name="llama",
                provider_adapter=settings.generate_llama_adapter,
                gateway_model=settings.generate_gateway_model,
                upstream_model=settings.generate_llama_upstream_model,
                currency="USD",
                input_cost_per_million=Decimal("0"),
                cached_input_cost_per_million=Decimal("0"),
                output_cost_per_million=Decimal("0"),
            )
        )
        provider_order.append("llama")
    if settings.generate_qwen_enabled:
        if ollama_client is None:
            raise RuntimeError("Ollama client is required when local providers are enabled")
        provider_registry["qwen"] = OllamaGenerateProvider(client=ollama_client, name="qwen")
        bootstraps.append(
            RouteBootstrap(
                provider_name="qwen",
                provider_adapter=settings.generate_qwen_adapter,
                gateway_model=settings.generate_gateway_model,
                upstream_model=settings.generate_qwen_upstream_model,
                currency="USD",
                input_cost_per_million=Decimal("0"),
                cached_input_cost_per_million=Decimal("0"),
                output_cost_per_million=Decimal("0"),
            )
        )
        provider_order.append("qwen")
    ledger = SqlAlchemyGatewayLedger(session_factory)
    return GenerationService(
        provider_registry=provider_registry,
        ledger=ledger,
        timeout_seconds=settings.provider_timeout_seconds,
        provider_order=provider_order,
        bootstraps=bootstraps,
        auto_routing_enabled=settings.auto_routing_enabled,
    )


app = create_app()


def run() -> None:
    """Run the gateway without Uvicorn's raw request-target access logs."""

    uvicorn.run("llm_gateway.main:app", access_log=False)
