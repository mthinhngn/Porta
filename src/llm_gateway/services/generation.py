"""Main orchestration for the R1 generate path."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import ParamSpec, TypeVar
from uuid import UUID

from llm_gateway.core.errors import ApiError
from llm_gateway.core.metrics import (
    record_generate_event,
    record_ledger_operation,
    record_provider_attempt,
)
from llm_gateway.core.task_routing import TASK_ROUTING_VERSION, classify_task
from llm_gateway.domain import GenerateCost, GenerateRequest, GenerateResponse, GenerateTokenUsage
from llm_gateway.persistence.ledger import GatewayLedger, GatewayRoute, RouteBootstrap, UsageCost
from llm_gateway.providers import (
    GenerateProvider,
    GenerateProviderContext,
    GenerateProviderResult,
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _ResolvedAttempt:
    route: GatewayRoute
    attempt_id: UUID
    result: GenerateProviderResult
    latency_ms: int


def _error_from_provider(error: ProviderError) -> ApiError:
    if isinstance(error, ProviderBadRequestError):
        return ApiError(
            message="Upstream provider rejected the request.",
            type="invalid_request_error",
            status_code=400,
            code=error.code,
        )
    if isinstance(error, ProviderAuthenticationError):
        return ApiError(
            message="Provider authentication failed.",
            type="server_error",
            status_code=502,
            code=error.code,
        )
    if isinstance(error, ProviderRateLimitError):
        return ApiError(
            message="Provider rate limit exceeded.",
            type="server_error",
            status_code=429,
            code=error.code,
        )
    if isinstance(error, ProviderTimeoutError):
        return ApiError(
            message="Provider request timed out.",
            type="server_error",
            status_code=504,
            code=error.code,
        )
    if isinstance(error, ProviderUnavailableError):
        return ApiError(
            message="Provider is unavailable.",
            type="server_error",
            status_code=503,
            code=error.code,
        )
    return ApiError(
        message="Provider request failed.",
        type="server_error",
        status_code=502,
        code=error.code,
    )


def _error_for_persistence(error: ProviderError) -> ProviderError:
    if error.code == "gateway_persistence_error":
        return error
    public_error = _error_from_provider(error)
    return type(error)(
        public_error.message,
        code=error.code,
        status_code=error.status_code,
    )


class GenerationService:
    """Coordinate provider execution with persistence and pricing."""

    def __init__(
        self,
        *,
        provider_registry: Mapping[str, GenerateProvider],
        ledger: GatewayLedger,
        timeout_seconds: float,
        provider_order: list[str] | tuple[str, ...] | None = None,
        bootstraps: tuple[RouteBootstrap, ...] | list[RouteBootstrap] | None = None,
    ) -> None:
        self._provider_registry = dict(provider_registry)
        self._ledger = ledger
        self._timeout_seconds = timeout_seconds
        self._provider_order = tuple(provider_order or provider_registry.keys())
        self._bootstraps = tuple(bootstraps or ())

    def bootstrap(self) -> None:
        if not self._bootstraps:
            return
        for config in self._bootstraps:
            self._ledger.ensure_r1_route(config)

    @staticmethod
    async def _run_ledger(callable_: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        started_at = perf_counter()
        task = asyncio.create_task(asyncio.to_thread(callable_, *args, **kwargs))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Database threads cannot be force-cancelled safely. Let the
            # transaction settle before the request task can release its lease.
            result = await task
        except Exception:
            record_ledger_operation(
                operation=getattr(callable_, "__name__", "unknown"),
                result="failure",
                duration_seconds=perf_counter() - started_at,
            )
            raise
        record_ledger_operation(
            operation=getattr(callable_, "__name__", "unknown"),
            result="success",
            duration_seconds=perf_counter() - started_at,
        )
        return result

    @property
    def cache_namespace(self) -> str:
        payload = {
            "provider_order": self._provider_order,
            "routes": [
                {
                    "provider": item.provider_name,
                    "adapter": item.provider_adapter,
                    "gateway_model": item.gateway_model,
                    "upstream_model": item.upstream_model,
                }
                for item in self._bootstraps
            ],
            "task_routing_version": TASK_ROUTING_VERSION,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def generate(
        self,
        request: GenerateRequest,
        *,
        correlation_id: str,
        allowed_providers: tuple[str, ...] | None = None,
        lease_validator: Callable[[], Awaitable[bool]] | None = None,
    ) -> GenerateResponse:
        provider_order = self._ordered_providers(request)
        resolved_routes = [
            await self._run_ledger(
                self._ledger.resolve_route_for_provider,
                request.model,
                provider_name,
            )
            for provider_name in provider_order
        ]
        configured_routes = [route for route in resolved_routes if route is not None]
        if not configured_routes:
            record_generate_event(
                stage="generate",
                result="failure",
                model_alias=request.model,
                error_code="model_not_found",
            )
            raise ApiError(
                message="Model is unavailable.",
                type="invalid_request_error",
                status_code=400,
                param="model",
                code="model_not_found",
            )
        allowed = set(allowed_providers) if allowed_providers is not None else None
        routes = [
            route
            for route in configured_routes
            if allowed is None or route.provider_name in allowed
        ]
        if not routes:
            record_generate_event(
                stage="generate",
                result="failure",
                model_alias=request.model,
                error_code="provider_access_denied",
            )
            raise ApiError(
                message="Provider access is not allowed.",
                type="invalid_request_error",
                status_code=403,
                code="provider_access_denied",
            )

        started_at = perf_counter()
        started_at_wall = datetime.now(UTC)
        primary_route = routes[0]
        request_id, attempt_id = await self._run_ledger(
            self._ledger.begin_generation,
            correlation_id=correlation_id,
            requested_model=request.model,
            route=primary_route,
            started_at=started_at_wall,
        )
        deadline_at = perf_counter() + self._timeout_seconds
        attempt_count = 0
        current_attempt_id = attempt_id
        last_error: ProviderError | None = None

        for route_index, route in enumerate(routes):
            same_provider_attempts = 2 if route.provider_name == "openai" else 1
            for provider_attempt_index in range(same_provider_attempts):
                if route_index != 0 or provider_attempt_index != 0:
                    remaining = deadline_at - perf_counter()
                    if remaining <= 0:
                        timeout_error = ProviderTimeoutError("Provider request timed out.")
                        await self._finalize_request_failure(
                            request_id=request_id,
                            error=timeout_error,
                        )
                        record_generate_event(
                            stage="generate",
                            result="failure",
                            provider=route.provider_name,
                            model_alias=route.gateway_model,
                            error_code=timeout_error.code,
                        )
                        raise _error_from_provider(timeout_error)
                    current_attempt_id = await self._run_ledger(
                        self._ledger.begin_attempt,
                        gateway_request_id=request_id,
                        route=route,
                        started_at=datetime.now(UTC),
                    )

                attempt_count += 1
                if lease_validator is not None and not await lease_validator():
                    coordination_error = ProviderUnavailableError(
                        "Cache coordination was lost.",
                        code="cache_coordination_lost",
                    )
                    await self._finalize_failure(
                        request_id=request_id,
                        attempt_id=current_attempt_id,
                        latency_started_at=perf_counter(),
                        error=coordination_error,
                        attempt_status="failed",
                    )
                    record_generate_event(
                        stage="generate",
                        result="failure",
                        provider=route.provider_name,
                        model_alias=route.gateway_model,
                        error_code=coordination_error.code,
                    )
                    raise ApiError(
                        message="Cache coordination was lost.",
                        type="server_error",
                        status_code=503,
                        code="service_unavailable",
                    )
                remaining = deadline_at - perf_counter()
                if remaining <= 0:
                    timeout_error = ProviderTimeoutError("Provider request timed out.")
                    await self._finalize_failure(
                        request_id=request_id,
                        attempt_id=current_attempt_id,
                        latency_started_at=perf_counter(),
                        error=timeout_error,
                        attempt_status="timed_out",
                    )
                    record_generate_event(
                        stage="generate",
                        result="failure",
                        provider=route.provider_name,
                        model_alias=route.gateway_model,
                        error_code=timeout_error.code,
                    )
                    raise _error_from_provider(timeout_error)

                outcome = await self._execute_attempt(
                    request=request,
                    request_id=request_id,
                    attempt_id=current_attempt_id,
                    route=route,
                    correlation_id=correlation_id,
                    timeout_seconds=min(self._timeout_seconds, remaining),
                )
                if isinstance(outcome, _ResolvedAttempt):
                    if lease_validator is not None and not await lease_validator():
                        coordination_error = ProviderUnavailableError(
                            "Cache coordination was lost.",
                            code="cache_coordination_lost",
                        )
                        await self._finalize_failure(
                            request_id=request_id,
                            attempt_id=current_attempt_id,
                            latency_started_at=perf_counter(),
                            error=coordination_error,
                            attempt_status="failed",
                        )
                        record_generate_event(
                            stage="generate",
                            result="failure",
                            provider=outcome.route.provider_name,
                            model_alias=outcome.route.gateway_model,
                            error_code=coordination_error.code,
                        )
                        raise ApiError(
                            message="Cache coordination was lost.",
                            type="server_error",
                            status_code=503,
                            code="service_unavailable",
                        )
                    usage_cost = await self._persist_success(
                        request_id=request_id,
                        attempt=outcome,
                    )
                    return self._response_from_result(
                        request_id=request_id,
                        route=outcome.route,
                        usage_cost=usage_cost,
                        output=outcome.result.output,
                        cache_status=outcome.result.cache_status,
                        latency_ms=round((perf_counter() - started_at) * 1000),
                        attempt_count=attempt_count,
                        routing_reason=self._routing_reason(
                            primary_provider=primary_route.provider_name,
                            winning_provider=outcome.route.provider_name,
                            attempt_count=attempt_count,
                        ),
                    )

                last_error = outcome
                is_retryable = outcome.retryable and outcome.code != "provider_not_configured"
                has_time_left = deadline_at - perf_counter() > 0
                is_primary_retry_slot = (
                    route.provider_name == "openai" and provider_attempt_index == 0
                )
                should_retry_same_provider = (
                    is_retryable and has_time_left and is_primary_retry_slot
                )
                if should_retry_same_provider:
                    continue
                if not is_retryable:
                    await self._finalize_request_failure(request_id=request_id, error=outcome)
                    record_generate_event(
                        stage="generate",
                        result="failure",
                        provider=route.provider_name,
                        model_alias=route.gateway_model,
                        error_code=outcome.code,
                    )
                    raise _error_from_provider(outcome)
                break

        if last_error is None:
            last_error = ProviderUnavailableError("Provider is unavailable.")
        await self._finalize_request_failure(request_id=request_id, error=last_error)
        record_generate_event(
            stage="generate",
            result="failure",
            model_alias=request.model,
            error_code=last_error.code,
        )
        raise _error_from_provider(last_error)

    def _ordered_providers(self, request: GenerateRequest) -> tuple[str, ...]:
        configured = set(self._provider_order)
        local_order = (
            ("qwen", "llama")
            if classify_task(request.input) == "coding"
            else (
                "llama",
                "qwen",
            )
        )
        return tuple(provider for provider in ("openai", *local_order) if provider in configured)

    def _response_from_result(
        self,
        *,
        request_id: UUID,
        route: GatewayRoute,
        usage_cost: UsageCost,
        output: str,
        cache_status: str,
        latency_ms: int,
        attempt_count: int,
        routing_reason: str,
    ) -> GenerateResponse:
        return GenerateResponse(
            request_id=request_id,
            output=output,
            provider=route.provider_name,
            model=route.gateway_model,
            tokens=GenerateTokenUsage(
                input_tokens=usage_cost.input_tokens,
                output_tokens=usage_cost.output_tokens,
                total_tokens=usage_cost.total_tokens,
            ),
            cost=GenerateCost(
                amount=usage_cost.estimated_cost,
                currency=usage_cost.currency,
            ),
            routing_reason=routing_reason,
            cache_status=cache_status,
            served_from_cache=False,
            attempt_count=attempt_count,
            latency_ms=latency_ms,
        )

    async def _execute_attempt(
        self,
        *,
        request: GenerateRequest,
        request_id: UUID,
        attempt_id: UUID,
        route: GatewayRoute,
        correlation_id: str,
        timeout_seconds: float,
    ) -> _ResolvedAttempt | ProviderError:
        provider = self._provider_registry.get(route.provider_name)
        started_at = perf_counter()
        if provider is None:
            missing_provider_error = ProviderUnavailableError(
                "Provider is unavailable.",
                code="provider_not_configured",
            )
            await self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=missing_provider_error,
                attempt_status="failed",
            )
            record_provider_attempt(
                provider=route.provider_name,
                model_alias=route.gateway_model,
                attempt_status="failed",
                error_code=missing_provider_error.code,
                duration_seconds=perf_counter() - started_at,
            )
            return missing_provider_error

        context = GenerateProviderContext(
            gateway_request_id=request_id,
            correlation_id=correlation_id,
            provider_name=route.provider_name,
            model_name=route.upstream_model,
            timeout_seconds=timeout_seconds,
            metadata={"routing_reason": route.routing_reason},
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await provider.generate(request, context)
        except TimeoutError:
            provider_error = ProviderTimeoutError("Provider request timed out.")
            await self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=provider_error,
                attempt_status="timed_out",
            )
            record_provider_attempt(
                provider=route.provider_name,
                model_alias=route.gateway_model,
                attempt_status="timed_out",
                error_code=provider_error.code,
                duration_seconds=perf_counter() - started_at,
            )
            return provider_error
        except ProviderError as error:
            attempt_status = "timed_out" if isinstance(error, ProviderTimeoutError) else "failed"
            await self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=error,
                attempt_status=attempt_status,
            )
            record_provider_attempt(
                provider=route.provider_name,
                model_alias=route.gateway_model,
                attempt_status=attempt_status,
                error_code=error.code,
                duration_seconds=perf_counter() - started_at,
            )
            return error
        except Exception:
            unexpected_provider_error = ProviderUnavailableError("Provider request failed.")
            await self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=unexpected_provider_error,
                attempt_status="failed",
            )
            record_provider_attempt(
                provider=route.provider_name,
                model_alias=route.gateway_model,
                attempt_status="failed",
                error_code=unexpected_provider_error.code,
                duration_seconds=perf_counter() - started_at,
            )
            return unexpected_provider_error
        record_provider_attempt(
            provider=route.provider_name,
            model_alias=route.gateway_model,
            attempt_status="succeeded",
            duration_seconds=perf_counter() - started_at,
        )
        return _ResolvedAttempt(
            route=route,
            attempt_id=attempt_id,
            result=result,
            latency_ms=round((perf_counter() - started_at) * 1000),
        )

    async def _persist_success(
        self,
        *,
        request_id: UUID,
        attempt: _ResolvedAttempt,
    ) -> UsageCost:
        completed_at = datetime.now(UTC)
        try:
            return await self._run_ledger(
                self._ledger.complete_generation,
                gateway_request_id=request_id,
                attempt_id=attempt.attempt_id,
                route=attempt.route,
                provider_request_id=attempt.result.provider_request_id,
                usage=attempt.result.usage,
                latency_ms=attempt.latency_ms,
                completed_at=completed_at,
            )
        except Exception:
            try:
                return await self._run_ledger(
                    self._ledger.reconcile_generation_success,
                    gateway_request_id=request_id,
                    attempt_id=attempt.attempt_id,
                    route=attempt.route,
                    provider_request_id=attempt.result.provider_request_id,
                    usage=attempt.result.usage,
                    latency_ms=attempt.latency_ms,
                    completed_at=completed_at,
                )
            except Exception as reconciliation_error:
                raise ApiError(
                    message="Gateway persistence failed.",
                    type="server_error",
                    status_code=500,
                    code="gateway_persistence_error",
                ) from reconciliation_error

    @staticmethod
    def _routing_reason(
        *,
        primary_provider: str,
        winning_provider: str,
        attempt_count: int,
    ) -> str:
        if winning_provider != primary_provider:
            return "fallback_after_retry"
        if attempt_count > 1:
            return "retry_after_error"
        return "configured_single_path"

    async def _finalize_intermediate_attempt(
        self,
        *,
        request_id: UUID,
        attempt_id: UUID,
        latency_started_at: float,
        error: ProviderError,
        attempt_status: str,
    ) -> None:
        latency_ms = round((perf_counter() - latency_started_at) * 1000)
        persisted_error = _error_for_persistence(error)
        try:
            await self._run_ledger(
                self._ledger.fail_attempt,
                request_id=request_id,
                attempt_id=attempt_id,
                attempt_status=attempt_status,
                latency_ms=latency_ms,
                error=persisted_error,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            return

    async def _finalize_failure(
        self,
        *,
        request_id: UUID,
        attempt_id: UUID,
        latency_started_at: float,
        error: ProviderError,
        attempt_status: str,
    ) -> None:
        latency_ms = round((perf_counter() - latency_started_at) * 1000)
        persisted_error = _error_for_persistence(error)
        try:
            await self._run_ledger(
                self._ledger.fail_generation,
                request_id=request_id,
                attempt_id=attempt_id,
                attempt_status=attempt_status,
                latency_ms=latency_ms,
                error=persisted_error,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            # Preserve the sanitized public failure if persistence also fails or
            # another worker already made the generation terminal.
            return

    async def _finalize_request_failure(
        self,
        *,
        request_id: UUID,
        error: ProviderError,
    ) -> None:
        persisted_error = _error_for_persistence(error)
        try:
            await self._run_ledger(
                self._ledger.fail_request,
                request_id=request_id,
                error=persisted_error,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            return
