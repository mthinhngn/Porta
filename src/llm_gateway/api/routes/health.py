"""Liveness and configuration-only readiness probes."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from llm_gateway.core.config import Settings
from llm_gateway.core.errors import ApiError

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
    return HealthResponse(status="ready")
