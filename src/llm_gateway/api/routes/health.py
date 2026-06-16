"""Liveness and configuration-only readiness probes."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from llm_gateway.core.config import Settings
from llm_gateway.core.errors import ApiError
from llm_gateway.core.ollama import missing_ollama_models
from llm_gateway.core.redis import RedisClient

router = APIRouter()


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["live", "ready"]


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="live")


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise ApiError(
            message="Application configuration is unavailable.",
            type="server_error",
            status_code=503,
            code="not_ready",
        )
    redis_client = getattr(request.app.state, "redis_client", None)
    if not isinstance(redis_client, RedisClient):
        raise ApiError(
            message="Redis is unavailable.",
            type="server_error",
            status_code=503,
            code="not_ready",
        )
    try:
        ping_ok = await redis_client.ping()
    except Exception as exc:
        raise ApiError(
            message="Redis is unavailable.",
            type="server_error",
            status_code=503,
            code="not_ready",
        ) from exc
    if ping_ok is not True:
        raise ApiError(
            message="Redis is unavailable.",
            type="server_error",
            status_code=503,
            code="not_ready",
        )
    required_models = {
        model
        for enabled, model in (
            (settings.generate_llama_enabled, settings.generate_llama_upstream_model),
            (settings.generate_qwen_enabled, settings.generate_qwen_upstream_model),
        )
        if enabled
    }
    if required_models:
        ollama_client = getattr(request.app.state, "ollama_client", None)
        if ollama_client is None:
            raise ApiError(
                message="Local model service is unavailable.",
                type="server_error",
                status_code=503,
                code="not_ready",
            )
        try:
            missing = await missing_ollama_models(ollama_client, required_models)
        except Exception as exc:
            raise ApiError(
                message="Local model service is unavailable.",
                type="server_error",
                status_code=503,
                code="not_ready",
            ) from exc
        if missing:
            raise ApiError(
                message="Required local models are unavailable.",
                type="server_error",
                status_code=503,
                code="not_ready",
            )
    return HealthResponse(status="ready")
