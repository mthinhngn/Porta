"""Redis client wiring for readiness and later Phase 2 features."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from redis.asyncio import Redis


@runtime_checkable
class RedisClient(Protocol):
    async def ping(self) -> bool: ...

    async def get(self, name: str) -> object: ...

    async def set(self, name: str, value: object, ex: int | None = None) -> object: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...

    async def aclose(self) -> None: ...


def build_redis_client(redis_url: str) -> Redis:
    return cast(
        Redis,
        Redis.from_url(redis_url, encoding="utf-8", decode_responses=True),
    )
