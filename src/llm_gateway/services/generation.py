"""Main orchestration for the R1 generate path."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from llm_gateway.core.errors import ApiError
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

    async def generate(
        self,
        request: GenerateRequest,
        *,
        correlation_id: str,
        allowed_providers: tuple[str, ...] | None = None,
    ) -> GenerateResponse:
        configured_routes = [
            route
            for route in (
                self._ledger.resolve_route_for_provider(request.model, provider_name)
                for provider_name in self._provider_order
            )
            if route is not None
        ]
        if not configured_routes:
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
            raise ApiError(
                message="Provider access is not allowed.",
                type="invalid_request_error",
                status_code=403,
                code="provider_access_denied",
            )

        started_at = perf_counter()
        started_at_wall = datetime.now(UTC)
        deadline_at = started_at + self._timeout_seconds
        primary_route = routes[0]
        request_id, attempt_id = self._ledger.begin_generation(
            correlation_id=correlation_id,
            requested_model=request.model,
            route=primary_route,
            started_at=started_at_wall,
        )
        attempt_count = 0
        current_attempt_id = attempt_id
        last_error: ProviderError | None = None

        for route_index, route in enumerate(routes):
            same_provider_attempts = 2 if route_index == 0 else 1
            for provider_attempt_index in range(same_provider_attempts):
                if route_index != 0 or provider_attempt_index != 0:
                    remaining = deadline_at - perf_counter()
                    if remaining <= 0:
                        timeout_error = ProviderTimeoutError("Provider request timed out.")
                        self._finalize_request_failure(
                            request_id=request_id,
                            error=timeout_error,
                        )
                        raise _error_from_provider(timeout_error)
                    current_attempt_id = self._ledger.begin_attempt(
                        gateway_request_id=request_id,
                        route=route,
                        started_at=datetime.now(UTC),
                    )

                attempt_count += 1
                remaining = deadline_at - perf_counter()
                if remaining <= 0:
                    timeout_error = ProviderTimeoutError("Provider request timed out.")
                    self._finalize_failure(
                        request_id=request_id,
                        attempt_id=current_attempt_id,
                        latency_started_at=perf_counter(),
                        error=timeout_error,
                        attempt_status="timed_out",
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
                    usage_cost = self._persist_success(
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
                is_primary_retry_slot = route_index == 0 and provider_attempt_index == 0
                should_retry_same_provider = (
                    is_retryable and has_time_left and is_primary_retry_slot
                )
                if should_retry_same_provider:
                    continue
                if not is_retryable:
                    self._finalize_request_failure(request_id=request_id, error=outcome)
                    raise _error_from_provider(outcome)
                break

        if last_error is None:
            last_error = ProviderUnavailableError("Provider is unavailable.")
        self._finalize_request_failure(request_id=request_id, error=last_error)
        raise _error_from_provider(last_error)

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
            self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=missing_provider_error,
                attempt_status="failed",
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
            self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=provider_error,
                attempt_status="timed_out",
            )
            return provider_error
        except ProviderError as error:
            attempt_status = "timed_out" if isinstance(error, ProviderTimeoutError) else "failed"
            self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=error,
                attempt_status=attempt_status,
            )
            return error
        except Exception:
            unexpected_provider_error = ProviderUnavailableError("Provider request failed.")
            self._finalize_intermediate_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=unexpected_provider_error,
                attempt_status="failed",
            )
            return unexpected_provider_error
        return _ResolvedAttempt(
            route=route,
            attempt_id=attempt_id,
            result=result,
            latency_ms=round((perf_counter() - started_at) * 1000),
        )

    def _persist_success(
        self,
        *,
        request_id: UUID,
        attempt: _ResolvedAttempt,
    ) -> UsageCost:
        completed_at = datetime.now(UTC)
        try:
            return self._ledger.complete_generation(
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
                return self._ledger.reconcile_generation_success(
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

    def _finalize_intermediate_attempt(
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
            self._ledger.fail_attempt(
                request_id=request_id,
                attempt_id=attempt_id,
                attempt_status=attempt_status,
                latency_ms=latency_ms,
                error=persisted_error,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            return

    def _finalize_failure(
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
            self._ledger.fail_generation(
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

    def _finalize_request_failure(
        self,
        *,
        request_id: UUID,
        error: ProviderError,
    ) -> None:
        persisted_error = _error_for_persistence(error)
        try:
            self._ledger.fail_request(
                request_id=request_id,
                error=persisted_error,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            return

    def _fail_with_api_error(
        self,
        *,
        request_id: UUID,
        attempt_id: UUID,
        latency_started_at: float,
        error: ProviderError,
        status_code: int,
        message: str,
        code: str,
    ) -> GenerateResponse:
        self._finalize_failure(
            request_id=request_id,
            attempt_id=attempt_id,
            latency_started_at=latency_started_at,
            error=error,
            attempt_status="failed",
        )
        raise ApiError(
            message=message,
            type="server_error",
            status_code=status_code,
            code=code,
        )
