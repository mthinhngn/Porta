"""ASGI application composition."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from llm_gateway.api.router import api_router
from llm_gateway.core.config import Settings, get_settings
from llm_gateway.core.errors import install_error_handlers
from llm_gateway.core.logging import configure_logging
from llm_gateway.core.middleware import CorrelationIdMiddleware
from llm_gateway.persistence.ledger import RouteBootstrap, SqlAlchemyGatewayLedger
from llm_gateway.providers.openai_responses import OpenAIResponsesProvider
from llm_gateway.services.generation import GenerationService


def create_app(
    settings: Settings | None = None,
    *,
    generation_service: GenerationService | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.generation_service = generation_service
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
    provider = OpenAIResponsesProvider(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        name=settings.generate_provider_name,
    )
    ledger = SqlAlchemyGatewayLedger(session_factory)
    bootstrap = RouteBootstrap(
        provider_name=settings.generate_provider_name,
        provider_adapter=settings.generate_provider_adapter,
        gateway_model=settings.generate_gateway_model,
        upstream_model=settings.generate_upstream_model,
        currency=settings.generate_currency,
        input_cost_per_million=settings.generate_input_cost_per_million,
        cached_input_cost_per_million=settings.generate_cached_input_cost_per_million,
        output_cost_per_million=settings.generate_output_cost_per_million,
    )
    return GenerationService(
        provider_registry={settings.generate_provider_name: provider},
        ledger=ledger,
        timeout_seconds=settings.provider_timeout_seconds,
        bootstrap=bootstrap,
    )


app = create_app()


def run() -> None:
    """Run the gateway without Uvicorn's raw request-target access logs."""

    uvicorn.run("llm_gateway.main:app", access_log=False)
