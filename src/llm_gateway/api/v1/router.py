"""Version 1 route composition."""

from fastapi import APIRouter, Request

from llm_gateway.core.errors import ApiError
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


@router.post("/generate", response_model=GenerateResponse, tags=["generate"])
async def generate(payload: GenerateRequest, request: Request) -> GenerateResponse:
    service = _generation_service(request)
    correlation_id = getattr(request.state, "correlation_id", None)
    if not isinstance(correlation_id, str):
        raise ApiError(
            message="Request correlation is unavailable.",
            type="server_error",
            status_code=500,
            code="missing_correlation_id",
        )
    return await service.generate(payload, correlation_id=correlation_id)
