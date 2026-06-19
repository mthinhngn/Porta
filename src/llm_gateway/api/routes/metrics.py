"""Prometheus scrape endpoint."""

from fastapi import APIRouter, Response

from llm_gateway.core.metrics import METRICS_CONTENT_TYPE, generate_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_metrics(), media_type=METRICS_CONTENT_TYPE)
