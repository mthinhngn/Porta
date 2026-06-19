"""Version 1 route composition."""

import asyncio
from contextlib import suppress
from time import perf_counter

from fastapi import APIRouter, Request

from llm_gateway.api.v1.analytics import router as analytics_router
from llm_gateway.core.auth import authenticated_actor
from llm_gateway.core.cache import CachePolicy, RedisResponseCache
from llm_gateway.core.errors import ApiError
from llm_gateway.core.guardrails import GuardrailPolicy, GuardrailService, raise_for_blocked
from llm_gateway.core.metrics import (
    record_cache_event,
    record_generate_duration,
    record_generate_event,
    record_guardrail_event,
    record_quota_event,
)
from llm_gateway.core.quota import RedisQuotaEnforcer, actor_quota_policy
from llm_gateway.domain import GenerateRequest, GenerateResponse
from llm_gateway.services import GenerationService

router = APIRouter()
router.include_router(analytics_router)


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
    started_at = perf_counter()
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
    guardrail_decision = guardrail_service.evaluate(payload)
    record_guardrail_event(
        result=guardrail_decision.outcome,
        error_code=guardrail_decision.reason_code,
    )
    try:
        raise_for_blocked(guardrail_decision)
    except ApiError as exc:
        record_generate_duration(
            result="failure",
            model_alias=payload.model,
            error_code=exc.code,
            duration_seconds=perf_counter() - started_at,
        )
        raise
    policy = actor_quota_policy(
        actor,
        window_seconds=getattr(request.app.state.settings, "gateway_quota_window_seconds", 60),
    )
    if policy is not None:
        enforcer = _quota_enforcer(request)
        if enforcer is None:
            record_quota_event(result="unavailable", error_code="service_unavailable")
            record_generate_duration(
                result="failure",
                model_alias=payload.model,
                error_code="service_unavailable",
                duration_seconds=perf_counter() - started_at,
            )
            raise ApiError(
                message="Quota service is unavailable.",
                type="server_error",
                status_code=503,
                code="service_unavailable",
            )
        try:
            await enforcer.enforce(policy)
        except ApiError as exc:
            quota_result = "exceeded" if exc.code == "quota_exceeded" else "unavailable"
            record_quota_event(result=quota_result, error_code=exc.code)
            record_generate_duration(
                result="failure",
                model_alias=payload.model,
                error_code=exc.code,
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_quota_event(result="allow")
    else:
        record_quota_event(result="disabled")
    response_cache = _response_cache(request)
    reservation = None
    lease_task = None
    cache_status = "disabled"
    if response_cache is None:
        record_cache_event(result="disabled", model_alias=payload.model, cache_status="disabled")
    else:
        try:
            lookup = await response_cache.get_or_reserve(
                actor_id=actor.actor_id,
                resolved_model=payload.model,
                request=payload,
                routing_namespace=service.cache_namespace,
                allowed_providers=actor.allowed_providers,
            )
        except ApiError as exc:
            record_cache_event(
                result="unavailable",
                model_alias=payload.model,
                cache_status="unavailable",
                error_code=exc.code,
            )
            record_generate_duration(
                result="failure",
                model_alias=payload.model,
                cache_status="unavailable",
                error_code=exc.code,
                duration_seconds=perf_counter() - started_at,
            )
            raise
        if lookup.response is not None:
            cache_status = "hit"
            record_cache_event(result="hit", model_alias=payload.model, cache_status="hit")
            record_generate_event(
                stage="generate",
                result="success",
                provider=lookup.response.provider,
                model_alias=lookup.response.model,
                cache_status="hit",
            )
            record_generate_duration(
                result="success",
                provider=lookup.response.provider,
                model_alias=lookup.response.model,
                cache_status="hit",
                duration_seconds=perf_counter() - started_at,
            )
            return lookup.response
        reservation = lookup.reservation
        if reservation is not None:
            cache_status = "miss"
            record_cache_event(result="reservation", model_alias=payload.model, cache_status="miss")
            lease_task = asyncio.create_task(response_cache.maintain(reservation))
        else:
            cache_status = "miss"
            record_cache_event(result="miss", model_alias=payload.model, cache_status="miss")
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
                    record_cache_event(
                        result="unavailable",
                        model_alias=payload.model,
                        cache_status="unavailable",
                        error_code="service_unavailable",
                    )
                    record_generate_duration(
                        result="failure",
                        model_alias=payload.model,
                        cache_status="unavailable",
                        error_code="service_unavailable",
                        duration_seconds=perf_counter() - started_at,
                    )
                    raise lease_error
                record_cache_event(
                    result="unavailable",
                    model_alias=payload.model,
                    cache_status="unavailable",
                    error_code="service_unavailable",
                )
                record_generate_duration(
                    result="failure",
                    model_alias=payload.model,
                    cache_status="unavailable",
                    error_code="service_unavailable",
                    duration_seconds=perf_counter() - started_at,
                )
                raise ApiError(
                    message="Cache coordination was lost.",
                    type="server_error",
                    status_code=503,
                    code="service_unavailable",
                )
        try:
            response = await generation_task
        except ApiError as exc:
            record_generate_duration(
                result="failure",
                model_alias=payload.model,
                cache_status=cache_status,
                error_code=exc.code,
                duration_seconds=perf_counter() - started_at,
            )
            raise
        if response_cache is not None and reservation is not None:
            publication_task = asyncio.create_task(
                response_cache.put(reservation=reservation, response=response)
            )
            try:
                await asyncio.shield(publication_task)
                record_cache_event(
                    result="success",
                    model_alias=response.model,
                    cache_status="publish_success",
                )
            except asyncio.CancelledError:
                # Do not release ownership until the atomic publication has
                # either committed or definitively failed.
                await publication_task
                raise
            except ApiError as exc:
                record_cache_event(
                    result="unavailable",
                    model_alias=response.model,
                    cache_status="publish_failure",
                    error_code=exc.code,
                )
                record_generate_duration(
                    result="failure",
                    provider=response.provider,
                    model_alias=response.model,
                    cache_status="publish_failure",
                    error_code=exc.code,
                    duration_seconds=perf_counter() - started_at,
                )
                raise
        record_generate_event(
            stage="generate",
            result="success",
            provider=response.provider,
            model_alias=response.model,
            cache_status=cache_status,
        )
        record_generate_duration(
            result="success",
            provider=response.provider,
            model_alias=response.model,
            cache_status=cache_status,
            duration_seconds=perf_counter() - started_at,
        )
        return response
    finally:
        if lease_task is not None:
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task
        if response_cache is not None and reservation is not None:
            await response_cache.release(reservation)
