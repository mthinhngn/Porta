"""Version 1 route composition."""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Request

from llm_gateway.core.auth import authenticated_actor
from llm_gateway.core.cache import CachePolicy, RedisResponseCache
from llm_gateway.core.errors import ApiError
from llm_gateway.core.guardrails import GuardrailPolicy, GuardrailService, raise_for_blocked
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


def _response_cache(request: Request) -> RedisResponseCache | None:
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None
    settings = request.app.state.settings
    encryption_key = settings.gateway_cache_encryption_key
    if encryption_key is None:
        return None
    return RedisResponseCache(
        redis_client,
        policy=CachePolicy(
            ttl_seconds=settings.gateway_cache_ttl_seconds,
            guardrail_version=settings.gateway_guardrail_version,
            encryption_key=encryption_key.get_secret_value(),
            lock_ttl_seconds=max(60, round(settings.provider_timeout_seconds) + 60),
            wait_timeout_seconds=settings.provider_timeout_seconds + 5,
        ),
    )


def _guardrail_service(request: Request) -> GuardrailService:
    settings = request.app.state.settings
    return GuardrailService(
        policy=GuardrailPolicy(
            version=settings.gateway_guardrail_version,
            test_block_token=settings.gateway_guardrail_test_block_token,
        )
    )


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
    guardrail_service = _guardrail_service(request)
    raise_for_blocked(guardrail_service.evaluate(payload))
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
    response_cache = _response_cache(request)
    reservation = None
    lease_task = None
    if response_cache is not None:
        lookup = await response_cache.get_or_reserve(
            actor_id=actor.actor_id,
            resolved_model=payload.model,
            request=payload,
            routing_namespace=service.cache_namespace,
            allowed_providers=actor.allowed_providers,
        )
        if lookup.response is not None:
            return lookup.response
        reservation = lookup.reservation
        if reservation is not None:
            lease_task = asyncio.create_task(response_cache.maintain(reservation))
    try:
        generation_task = asyncio.create_task(
            service.generate(
                payload,
                correlation_id=correlation_id,
                allowed_providers=actor.allowed_providers,
                lease_validator=(
                    (lambda: response_cache.refresh(reservation))
                    if response_cache is not None and reservation is not None
                    else None
                ),
            )
        )
        if lease_task is not None:
            try:
                completed, _ = await asyncio.wait(
                    {generation_task, lease_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # A client disconnect must not release the cache lease while a
                # ledger thread can still commit usage.
                with suppress(Exception):
                    await asyncio.shield(generation_task)
                raise
            if lease_task in completed:
                lease_error = lease_task.exception()
                with suppress(Exception):
                    await asyncio.shield(generation_task)
                if lease_error is not None:
                    raise lease_error
                raise ApiError(
                    message="Cache coordination was lost.",
                    type="server_error",
                    status_code=503,
                    code="service_unavailable",
                )
        response = await generation_task
        if response_cache is not None and reservation is not None:
            publication_task = asyncio.create_task(
                response_cache.put(reservation=reservation, response=response)
            )
            try:
                await asyncio.shield(publication_task)
            except asyncio.CancelledError:
                # Do not release ownership until the atomic publication has
                # either committed or definitively failed.
                await publication_task
                raise
        return response
    finally:
        if lease_task is not None:
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task
        if response_cache is not None and reservation is not None:
            await response_cache.release(reservation)
