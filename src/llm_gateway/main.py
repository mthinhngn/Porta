"""ASGI application composition."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_gateway.api.router import api_router
from llm_gateway.core.config import Settings, get_settings
from llm_gateway.core.errors import install_error_handlers
from llm_gateway.core.logging import configure_logging
from llm_gateway.core.middleware import CorrelationIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        yield

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


app = create_app()
