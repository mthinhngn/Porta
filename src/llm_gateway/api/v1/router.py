"""Version 1 route composition."""

from fastapi import APIRouter, Request

from llm_gateway.core.auth import authenticated_actor
from llm_gateway.core.errors import ApiError
from llm_gateway.core.quota import RedisQuotaEnforcer, actor_quota_policy
from llm_gateway.domain import GenerateRequest, GenerateResponse
from llm_gateway.services import GenerationService

router = APIRouter()


def _generation_service(request: Request) -> GenerationService:
    service = getattr(request.app.state, "generation_service", None)
    if not isinstance(service, GenerationService):
        raise ApiError(
            message="Generation service is not configured.",
            type="server_error",
            status_code=503,
            code="service_unavailable",
        )
    return service


def _quota_enforcer(request: Request) -> RedisQuotaEnforcer | None:
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None
    return RedisQuotaEnforcer(redis_client)


@router.post("/generate", response_model=GenerateResponse, tags=["generate"])
async def generate(payload: GenerateRequest, request: Request) -> GenerateResponse:
    service = _generation_service(request)
    actor = authenticated_actor(request)
    correlation_id = getattr(request.state, "correlation_id", None)
    if not isinstance(correlation_id, str):
        raise ApiError(
            message="Request correlation is unavailable.",
            type="server_error",
            status_code=500,
            code="missing_correlation_id",
        )
    policy = actor_quota_policy(
        actor,
        window_seconds=getattr(request.app.state.settings, "gateway_quota_window_seconds", 60),
    )
    if policy is not None:
        enforcer = _quota_enforcer(request)
        if enforcer is None:
            raise ApiError(
                message="Quota service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            )
        await enforcer.enforce(policy)
    return await service.generate(payload, correlation_id=correlation_id)
