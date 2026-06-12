"""Main orchestration for the R1 generate path."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from llm_gateway.core.errors import ApiError
from llm_gateway.domain import GenerateCost, GenerateRequest, GenerateResponse, GenerateTokenUsage
from llm_gateway.persistence.ledger import GatewayLedger, GatewayRoute, RouteBootstrap, UsageCost
from llm_gateway.providers import (
    GenerateProvider,
    GenerateProviderContext,
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


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


class GenerationService:
    """Coordinate provider execution with persistence and pricing."""

    def __init__(
        self,
        *,
        provider_registry: Mapping[str, GenerateProvider],
        ledger: GatewayLedger,
        timeout_seconds: float,
        bootstrap: RouteBootstrap | None = None,
    ) -> None:
        self._provider_registry = dict(provider_registry)
        self._ledger = ledger
        self._timeout_seconds = timeout_seconds
        self._bootstrap = bootstrap

    def bootstrap(self) -> None:
        if self._bootstrap is None:
            return
        self._ledger.ensure_r1_route(self._bootstrap)

    async def generate(
        self,
        request: GenerateRequest,
        *,
        correlation_id: str,
    ) -> GenerateResponse:
        route = self._ledger.resolve_route(request.model)
        if route is None:
            raise ApiError(
                message="Model is unavailable.",
                type="invalid_request_error",
                status_code=400,
                param="model",
                code="model_not_found",
            )

        request_id = self._ledger.create_gateway_request(
            correlation_id=correlation_id,
            requested_model=request.model,
        )
        attempt_id = self._ledger.create_provider_attempt(
            gateway_request_id=request_id,
            route=route,
        )
        started_at = perf_counter()
        started_at_wall = datetime.now(UTC)
        self._ledger.start_generation(
            request_id=request_id,
            attempt_id=attempt_id,
            started_at=started_at_wall,
        )

        context = GenerateProviderContext(
            gateway_request_id=request_id,
            correlation_id=correlation_id,
            provider_name=route.provider_name,
            model_name=route.upstream_model,
            timeout_seconds=self._timeout_seconds,
            metadata={"routing_reason": route.routing_reason},
        )
        provider = self._provider_registry.get(route.provider_name)
        if provider is None:
            return self._fail_with_api_error(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=ProviderUnavailableError(
                    "Provider is unavailable.",
                    code="provider_not_configured",
                ),
                status_code=500,
                message="Resolved provider is not configured.",
                code="provider_not_configured",
            )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await provider.generate(request, context)
        except TimeoutError as exc:
            provider_error = ProviderTimeoutError("Provider request timed out.")
            self._finalize_failure(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=provider_error,
                attempt_status="timed_out",
            )
            raise _error_from_provider(provider_error) from exc
        except ProviderError as error:
            attempt_status = "timed_out" if isinstance(error, ProviderTimeoutError) else "failed"
            self._finalize_failure(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=error,
                attempt_status=attempt_status,
            )
            raise _error_from_provider(error) from error
        except Exception as exc:
            unexpected_error = ProviderUnavailableError("Provider request failed.")
            self._finalize_failure(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=unexpected_error,
                attempt_status="failed",
            )
            raise _error_from_provider(unexpected_error) from exc

        latency_ms = round((perf_counter() - started_at) * 1000)
        completed_at = datetime.now(UTC)
        try:
            usage_cost = self._ledger.complete_generation(
                gateway_request_id=request_id,
                attempt_id=attempt_id,
                route=route,
                provider_request_id=result.provider_request_id,
                usage=result.usage,
                latency_ms=latency_ms,
                completed_at=completed_at,
            )
        except Exception as exc:
            persistence_error = ProviderUnavailableError(
                "Gateway persistence failed.",
                code="gateway_persistence_error",
            )
            self._finalize_failure(
                request_id=request_id,
                attempt_id=attempt_id,
                latency_started_at=started_at,
                error=persistence_error,
                attempt_status="failed",
            )
            raise ApiError(
                message="Gateway persistence failed.",
                type="server_error",
                status_code=500,
                code="gateway_persistence_error",
            ) from exc
        return self._response_from_result(
            request_id=request_id,
            route=route,
            usage_cost=usage_cost,
            output=result.output,
            cache_status=result.cache_status,
            latency_ms=latency_ms,
        )

    def _response_from_result(
        self,
        *,
        request_id: UUID,
        route: GatewayRoute,
        usage_cost: UsageCost,
        output: str,
        cache_status: str,
        latency_ms: int,
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
            routing_reason=route.routing_reason,
            cache_status=cache_status,
            latency_ms=latency_ms,
        )

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
        self._ledger.fail_generation(
            request_id=request_id,
            attempt_id=attempt_id,
            attempt_status=attempt_status,
            latency_ms=latency_ms,
            error=error,
            completed_at=datetime.now(UTC),
        )

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
