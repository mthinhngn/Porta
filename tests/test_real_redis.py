from __future__ import annotations

import asyncio
import base64
import os
from uuid import UUID

import pytest

from llm_gateway.core.cache import CachePolicy, RedisResponseCache
from llm_gateway.core.errors import ApiError
from llm_gateway.core.quota import QuotaPolicy, RedisQuotaEnforcer, quota_key
from llm_gateway.core.redis import build_redis_client
from llm_gateway.domain import GenerateCost, GenerateRequest, GenerateResponse, GenerateTokenUsage

pytestmark = pytest.mark.skipif(
    os.getenv("LLM_GATEWAY_REAL_REDIS_TEST") != "1",
    reason="set LLM_GATEWAY_REAL_REDIS_TEST=1 to run real Redis concurrency tests",
)

REDIS_URL = os.getenv("LLM_GATEWAY_REDIS_URL", "redis://127.0.0.1:6379/0")


def test_real_redis_quota_is_atomic_and_actor_scoped() -> None:
    async def exercise() -> None:
        client = build_redis_client(REDIS_URL)
        enforcer = RedisQuotaEnforcer(client)
        actor_a = UUID("00000000-0000-0000-0000-000000000701")
        actor_b = UUID("00000000-0000-0000-0000-000000000702")
        await client.delete(quota_key(actor_a), quota_key(actor_b))

        async def attempt(actor_id: UUID) -> int:
            try:
                await enforcer.enforce(
                    QuotaPolicy(actor_id=actor_id, request_limit=1, window_seconds=60)
                )
            except ApiError as exc:
                return exc.status_code
            return 200

        actor_a_results = await asyncio.gather(attempt(actor_a), attempt(actor_a))
        actor_b_result = await attempt(actor_b)

        assert sorted(actor_a_results) == [200, 429]
        assert actor_b_result == 200
        assert await client.get(quota_key(actor_a)) == "1"
        assert await client.get(quota_key(actor_b)) == "1"
        await client.delete(quota_key(actor_a), quota_key(actor_b))
        await client.aclose()

    asyncio.run(exercise())


def test_real_redis_stale_cache_owner_cannot_publish() -> None:
    async def exercise() -> None:
        client = build_redis_client(REDIS_URL)
        cache = RedisResponseCache(
            client,
            policy=CachePolicy(
                ttl_seconds=60,
                guardrail_version="real-redis-v1",
                encryption_key=base64.urlsafe_b64encode(b"r" * 32).decode(),
                lock_ttl_seconds=60,
                wait_timeout_seconds=1,
            ),
        )
        request = GenerateRequest(model="gateway-default", input="real redis ownership test")
        lookup = await cache.get_or_reserve(
            actor_id=UUID("00000000-0000-0000-0000-000000000703"),
            resolved_model=request.model,
            request=request,
            routing_namespace="real-redis-route-v1",
            allowed_providers=None,
        )
        assert lookup.reservation is not None
        await client.set(lookup.reservation.lock_key, "new-owner", ex=60)
        with pytest.raises(ApiError) as error:
            await cache.put(
                reservation=lookup.reservation,
                response=GenerateResponse(
                    request_id=UUID("00000000-0000-0000-0000-000000000704"),
                    output="must not publish",
                    provider="openai",
                    model="gateway-default",
                    tokens=GenerateTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    cost=GenerateCost(amount="0.01", currency="USD"),
                    routing_reason="configured_single_path",
                    cache_status="miss",
                    served_from_cache=False,
                    attempt_count=1,
                    latency_ms=1,
                ),
            )
        assert error.value.message == "Cache coordination was lost."

        assert await client.get(lookup.reservation.key) is None
        await client.delete(lookup.reservation.key, lookup.reservation.lock_key)
        await client.aclose()

    asyncio.run(exercise())


def test_real_redis_cache_execution_lock_cannot_expire_or_transfer() -> None:
    async def exercise() -> None:
        client = build_redis_client(REDIS_URL)
        cache = RedisResponseCache(
            client,
            policy=CachePolicy(
                ttl_seconds=60,
                guardrail_version="real-redis-refresh-v1",
                encryption_key=base64.urlsafe_b64encode(b"s" * 32).decode(),
                lock_ttl_seconds=1,
                wait_timeout_seconds=0.1,
            ),
        )
        request = GenerateRequest(model="gateway-default", input="real redis lease refresh")
        lookup = await cache.get_or_reserve(
            actor_id=UUID("00000000-0000-0000-0000-000000000705"),
            resolved_model=request.model,
            request=request,
            routing_namespace="real-redis-route-v1",
            allowed_providers=None,
        )
        assert lookup.reservation is not None
        await asyncio.sleep(2.2)
        assert await client.get(lookup.reservation.lock_key) == lookup.reservation.owner_token
        assert await client.ttl(lookup.reservation.lock_key) == -1
        with pytest.raises(ApiError) as error:
            await cache.get_or_reserve(
                actor_id=UUID("00000000-0000-0000-0000-000000000705"),
                resolved_model=request.model,
                request=request,
                routing_namespace="real-redis-route-v1",
                allowed_providers=None,
            )
        assert error.value.message == "Cache request is still in progress."

        await cache.release(lookup.reservation)
        await client.delete(lookup.reservation.key, lookup.reservation.lock_key)
        await client.aclose()

    asyncio.run(exercise())
