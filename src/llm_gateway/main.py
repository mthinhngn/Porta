"""ASGI application composition."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

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
from llm_gateway.core.redis import RedisClient, build_redis_client
from llm_gateway.persistence.ledger import RouteBootstrap, SqlAlchemyGatewayLedger
from llm_gateway.providers import (
    AnthropicMessagesProvider,
    GeminiGenerateContentProvider,
    GenerateProvider,
    OpenAIResponsesProvider,
)
from llm_gateway.services.generation import GenerationService


def create_app(
    settings: Settings | None = None,
    *,
    generation_service: GenerationService | None = None,
    session_factory: sessionmaker[Session] | None = None,
    redis_client: RedisClient | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.generation_service = generation_service
        app.state.redis_client = redis_client
        if app.state.redis_client is None and app_settings.redis_url is not None:
            app.state.redis_client = build_redis_client(app_settings.redis_url)
        if app.state.generation_service is None and session_factory is not None:
            app.state.generation_service = _build_generation_service(
                settings=app_settings,
                session_factory=session_factory,
            )
        elif app.state.generation_service is None and app_settings.database_url is not None:
            engine = create_engine(app_settings.database_url)
            app.state.engine = engine
            db_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            app.state.session_factory = db_session_factory
            app.state.generation_service = _build_generation_service(
                settings=app_settings,
                session_factory=db_session_factory,
            )
        service = getattr(app.state, "generation_service", None)
        if isinstance(service, GenerationService):
            service.bootstrap()
        yield
        teardown_redis = cast(RedisClient | None, getattr(app.state, "redis_client", None))
        if teardown_redis is not None:
            await teardown_redis.aclose()
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
    if settings.generate_anthropic_enabled:
        provider_registry["anthropic"] = AnthropicMessagesProvider(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
            name="anthropic",
        )
        bootstraps.append(
            RouteBootstrap(
                provider_name="anthropic",
                provider_adapter=settings.generate_anthropic_adapter,
                gateway_model=settings.generate_gateway_model,
                upstream_model=settings.generate_anthropic_upstream_model,
                currency=settings.generate_anthropic_currency,
                input_cost_per_million=settings.generate_anthropic_input_cost_per_million,
                cached_input_cost_per_million=settings.generate_anthropic_cached_input_cost_per_million,
                output_cost_per_million=settings.generate_anthropic_output_cost_per_million,
            )
        )
        provider_order.append("anthropic")
    if settings.generate_gemini_enabled:
        provider_registry["gemini"] = GeminiGenerateContentProvider(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            name="gemini",
        )
        bootstraps.append(
            RouteBootstrap(
                provider_name="gemini",
                provider_adapter=settings.generate_gemini_adapter,
                gateway_model=settings.generate_gateway_model,
                upstream_model=settings.generate_gemini_upstream_model,
                currency=settings.generate_gemini_currency,
                input_cost_per_million=settings.generate_gemini_input_cost_per_million,
                cached_input_cost_per_million=settings.generate_gemini_cached_input_cost_per_million,
                output_cost_per_million=settings.generate_gemini_output_cost_per_million,
            )
        )
        provider_order.append("gemini")
    ledger = SqlAlchemyGatewayLedger(session_factory)
    return GenerationService(
        provider_registry=provider_registry,
        ledger=ledger,
        timeout_seconds=settings.provider_timeout_seconds,
        provider_order=provider_order,
        bootstraps=bootstraps,
    )


app = create_app()


def run() -> None:
    """Run the gateway without Uvicorn's raw request-target access logs."""

    uvicorn.run("llm_gateway.main:app", access_log=False)
