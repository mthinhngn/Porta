"""Per-actor Redis response cache for Phase 2."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from llm_gateway.core.errors import ApiError
from llm_gateway.core.redis import RedisClient
from llm_gateway.domain import GenerateRequest, GenerateResponse


def _request_fingerprint(request: GenerateRequest) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_key(
    *,
    actor_id: UUID,
    resolved_model: str,
    guardrail_version: str,
    request: GenerateRequest,
) -> str:
    fingerprint = _request_fingerprint(request)
    return (
        "llm-gateway:cache:"
        f"actor:{actor_id}:"
        f"model:{resolved_model}:"
        f"guardrail:{guardrail_version}:"
        f"req:{fingerprint}"
    )


@dataclass(frozen=True, slots=True)
class CachePolicy:
    ttl_seconds: int
    guardrail_version: str


class RedisResponseCache:
    def __init__(self, redis_client: RedisClient, *, policy: CachePolicy) -> None:
        self._redis_client = redis_client
        self._policy = policy

    async def get(
        self,
        *,
        actor_id: UUID,
        resolved_model: str,
        request: GenerateRequest,
    ) -> GenerateResponse | None:
        key = cache_key(
            actor_id=actor_id,
            resolved_model=resolved_model,
            guardrail_version=self._policy.guardrail_version,
            request=request,
        )
        try:
            cached = await self._redis_client.get(key)
        except Exception as exc:
            raise ApiError(
                message="Cache service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            ) from exc
        if cached is None:
            return None
        if not isinstance(cached, str):
            raise ApiError(
                message="Cache service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            )
        response = GenerateResponse.model_validate_json(cached)
        return response.model_copy(update={"served_from_cache": True})

    async def put(
        self,
        *,
        actor_id: UUID,
        resolved_model: str,
        request: GenerateRequest,
        response: GenerateResponse,
    ) -> None:
        key = cache_key(
            actor_id=actor_id,
            resolved_model=resolved_model,
            guardrail_version=self._policy.guardrail_version,
            request=request,
        )
        payload = response.model_copy(update={"served_from_cache": False}).model_dump_json()
        try:
            await self._redis_client.set(key, payload, ex=self._policy.ttl_seconds)
        except Exception:
            # A cache write failure should not convert a successful provider
            # response into a client-visible error.
            return
