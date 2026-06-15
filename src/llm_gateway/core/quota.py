"""Per-actor request quota enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from llm_gateway.core.errors import ApiError
from llm_gateway.domain import AuthenticatedActor

QUOTA_INCREMENT_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current and tonumber(current) >= tonumber(ARGV[1]) then
  return 0
end
local updated = redis.call('INCR', KEYS[1])
if updated == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
if updated > tonumber(ARGV[1]) then
  return 0
end
return updated
"""


class RedisQuotaClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    actor_id: UUID
    request_limit: int
    window_seconds: int


def actor_quota_policy(
    actor: AuthenticatedActor,
    *,
    window_seconds: int,
) -> QuotaPolicy | None:
    if actor.request_quota_limit is None:
        return None
    return QuotaPolicy(
        actor_id=actor.actor_id,
        request_limit=actor.request_quota_limit,
        window_seconds=window_seconds,
    )


def quota_key(actor_id: UUID) -> str:
    return f"llm-gateway:quota:actor:{actor_id}:requests"


class RedisQuotaEnforcer:
    def __init__(self, redis_client: RedisQuotaClient) -> None:
        self._redis_client = redis_client

    async def enforce(self, policy: QuotaPolicy) -> None:
        try:
            result = await self._redis_client.eval(
                QUOTA_INCREMENT_SCRIPT,
                1,
                quota_key(policy.actor_id),
                policy.request_limit,
                policy.window_seconds,
            )
        except Exception as exc:
            raise ApiError(
                message="Quota service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            ) from exc
        if not isinstance(result, int):
            raise ApiError(
                message="Quota service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            )
        if result == 0:
            raise ApiError(
                message="Quota exceeded.",
                type="server_error",
                status_code=429,
                code="quota_exceeded",
            )
