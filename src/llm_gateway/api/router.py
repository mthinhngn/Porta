"""Top-level API router composition."""

from fastapi import APIRouter

from llm_gateway.api.routes.health import router as health_router
from llm_gateway.api.v1.router import router as v1_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(v1_router, prefix="/v1")
