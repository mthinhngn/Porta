"""Persistence helpers for gateway request lifecycle and pricing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from llm_gateway.persistence.models import (
    GatewayRequest,
    Model,
    PricingSnapshot,
    Provider,
    ProviderAttempt,
    UsageRecord,
)
from llm_gateway.providers import ProviderError, ProviderTokenUsage

COST_SCALE = Decimal("0.0000000001")
TOKENS_PER_MILLION = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class RouteBootstrap:
    provider_name: str
    provider_adapter: str
    gateway_model: str
    upstream_model: str
    currency: str
    input_cost_per_million: Decimal
    cached_input_cost_per_million: Decimal
    output_cost_per_million: Decimal


@dataclass(frozen=True, slots=True)
class GatewayRoute:
    provider_id: UUID
    provider_name: str
    model_id: UUID
    gateway_model: str
    upstream_model: str
    routing_reason: str


@dataclass(frozen=True, slots=True)
class UsageCost:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: Decimal
    currency: str


def calculate_estimated_cost(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    input_cost_per_million: Decimal,
    cached_input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
) -> Decimal:
    if cached_input_tokens < 0 or cached_input_tokens > input_tokens:
        raise ValueError("cached_input_tokens must be between zero and input_tokens")
    uncached_input_tokens = input_tokens - cached_input_tokens
    total = (
        Decimal(uncached_input_tokens) * input_cost_per_million / TOKENS_PER_MILLION
        + Decimal(cached_input_tokens) * cached_input_cost_per_million / TOKENS_PER_MILLION
        + Decimal(output_tokens) * output_cost_per_million / TOKENS_PER_MILLION
    )
    return total.quantize(COST_SCALE, rounding=ROUND_HALF_UP)


class GatewayLedger(Protocol):
    def ensure_r1_route(self, config: RouteBootstrap) -> None: ...

    def resolve_route(self, requested_model: str) -> GatewayRoute | None: ...

    def begin_generation(
        self,
        *,
        correlation_id: str,
        requested_model: str,
        route: GatewayRoute,
        started_at: datetime,
    ) -> tuple[UUID, UUID]: ...

    def fail_generation(
        self,
        *,
        request_id: UUID,
        attempt_id: UUID,
        attempt_status: str,
        latency_ms: int,
        error: ProviderError,
        completed_at: datetime,
    ) -> None: ...

    def complete_generation(
        self,
        *,
        gateway_request_id: UUID,
        attempt_id: UUID,
        route: GatewayRoute,
        provider_request_id: str | None,
        usage: ProviderTokenUsage,
        latency_ms: int,
        completed_at: datetime,
    ) -> UsageCost: ...


class SqlAlchemyGatewayLedger(GatewayLedger):
    """Synchronous persistence helpers used by the main generation service."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ensure_r1_route(self, config: RouteBootstrap) -> None:
        with self._session_factory.begin() as session:
            provider = session.scalar(select(Provider).where(Provider.name == config.provider_name))
            if provider is None:
                provider = Provider(name=config.provider_name, adapter=config.provider_adapter)
                session.add(provider)
                session.flush()
            else:
                provider.adapter = config.provider_adapter
                provider.enabled = True

            model = session.scalar(
                select(Model).where(
                    Model.provider_id == provider.id,
                    Model.upstream_name == config.upstream_model,
                )
            )
            if model is None:
                model = Model(
                    provider_id=provider.id,
                    gateway_name=config.gateway_model,
                    upstream_name=config.upstream_model,
                )
                session.add(model)
                session.flush()
            else:
                model.gateway_name = config.gateway_model
                model.enabled = True

            obsolete_models = session.scalars(
                select(Model).where(
                    Model.gateway_name == config.gateway_model,
                    Model.id != model.id,
                )
            ).all()
            for obsolete in obsolete_models:
                obsolete.enabled = False

            current_pricing = session.scalar(
                select(PricingSnapshot)
                .where(
                    PricingSnapshot.provider_id == provider.id,
                    PricingSnapshot.model_id == model.id,
                )
                .order_by(desc(PricingSnapshot.effective_at))
                .limit(1)
            )
            if current_pricing is None or (
                current_pricing.currency != config.currency
                or current_pricing.input_cost_per_million != config.input_cost_per_million
                or (
                    current_pricing.cached_input_cost_per_million
                    != config.cached_input_cost_per_million
                )
                or current_pricing.output_cost_per_million != config.output_cost_per_million
            ):
                session.add(
                    PricingSnapshot(
                        provider_id=provider.id,
                        model_id=model.id,
                        currency=config.currency,
                        input_cost_per_million=config.input_cost_per_million,
                        cached_input_cost_per_million=config.cached_input_cost_per_million,
                        output_cost_per_million=config.output_cost_per_million,
                    )
                )

    def resolve_route(self, requested_model: str) -> GatewayRoute | None:
        with self._session_factory() as session:
            row = session.execute(
                select(Model, Provider)
                .join(Provider, Provider.id == Model.provider_id)
                .where(
                    Model.gateway_name == requested_model,
                    Model.enabled.is_(True),
                    Provider.enabled.is_(True),
                )
                .order_by(Model.created_at.asc())
            ).first()
            if row is None:
                return None

            model, provider = row
            return GatewayRoute(
                provider_id=provider.id,
                provider_name=provider.name,
                model_id=model.id,
                gateway_model=model.gateway_name,
                upstream_model=model.upstream_name,
                routing_reason="configured_single_path",
            )

    def begin_generation(
        self,
        *,
        correlation_id: str,
        requested_model: str,
        route: GatewayRoute,
        started_at: datetime,
    ) -> tuple[UUID, UUID]:
        with self._session_factory.begin() as session:
            record = GatewayRequest(
                correlation_id=correlation_id,
                requested_model=requested_model,
                status="in_progress",
                started_at=started_at,
            )
            session.add(record)
            session.flush()
            attempt = ProviderAttempt(
                gateway_request_id=record.id,
                provider_id=route.provider_id,
                model_id=route.model_id,
                attempt_number=1,
                status="in_progress",
                started_at=started_at,
            )
            session.add(attempt)
            session.flush()
            return record.id, attempt.id

    def fail_generation(
        self,
        *,
        request_id: UUID,
        attempt_id: UUID,
        attempt_status: str,
        latency_ms: int,
        error: ProviderError,
        completed_at: datetime,
    ) -> None:
        if attempt_status not in {"failed", "timed_out", "cancelled"}:
            raise ValueError("attempt_status must be a terminal failure status")
        with self._session_factory.begin() as session:
            record = session.get(GatewayRequest, request_id, with_for_update=True)
            attempt = session.get(ProviderAttempt, attempt_id, with_for_update=True)
            if record is None or attempt is None:
                raise RuntimeError("Generation request or provider attempt does not exist.")
            if attempt.gateway_request_id != record.id:
                raise RuntimeError("Provider attempt does not belong to the generation request.")
            if record.status != "in_progress" or attempt.status != "in_progress":
                raise RuntimeError("Generation request and provider attempt must be in progress.")
            attempt.status = attempt_status
            attempt.latency_ms = latency_ms
            attempt.error_type = type(error).__name__
            attempt.error_code = error.code
            attempt.error_message = error.message
            attempt.completed_at = completed_at
            record.status = "failed"
            record.error_type = type(error).__name__
            record.error_code = error.code
            record.error_message = error.message
            record.completed_at = completed_at

    def complete_generation(
        self,
        *,
        gateway_request_id: UUID,
        attempt_id: UUID,
        route: GatewayRoute,
        provider_request_id: str | None,
        usage: ProviderTokenUsage,
        latency_ms: int,
        completed_at: datetime,
    ) -> UsageCost:
        with self._session_factory.begin() as session:
            request = session.get(
                GatewayRequest,
                gateway_request_id,
                with_for_update=True,
            )
            attempt = session.get(
                ProviderAttempt,
                attempt_id,
                with_for_update=True,
            )
            if request is None or attempt is None:
                raise RuntimeError("Generation request or provider attempt does not exist.")
            if attempt.gateway_request_id != request.id:
                raise RuntimeError("Provider attempt does not belong to the generation request.")
            if attempt.provider_id != route.provider_id or attempt.model_id != route.model_id:
                raise RuntimeError("Provider attempt does not match the selected route.")
            if request.status != "in_progress" or attempt.status != "in_progress":
                raise RuntimeError("Generation request and provider attempt must be in progress.")

            pricing = self._pricing_snapshot(session, route, effective_at=completed_at)
            estimated_cost = calculate_estimated_cost(
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                input_cost_per_million=pricing.input_cost_per_million,
                cached_input_cost_per_million=pricing.cached_input_cost_per_million,
                output_cost_per_million=pricing.output_cost_per_million,
            )

            usage_record = UsageRecord(
                gateway_request_id=gateway_request_id,
                provider_attempt_id=attempt_id,
                pricing_snapshot_id=pricing.id,
                prompt_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                completion_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost=estimated_cost,
                currency=pricing.currency,
            )
            session.add(usage_record)

            attempt.status = "succeeded"
            attempt.latency_ms = latency_ms
            attempt.upstream_request_id = provider_request_id
            attempt.completed_at = completed_at

            request.status = "succeeded"
            request.completed_at = completed_at

            return UsageCost(
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost=estimated_cost,
                currency=pricing.currency,
            )

    def _pricing_snapshot(
        self,
        session: Session,
        route: GatewayRoute,
        *,
        effective_at: datetime,
    ) -> PricingSnapshot:
        pricing = session.scalar(
            select(PricingSnapshot)
            .where(
                PricingSnapshot.provider_id == route.provider_id,
                PricingSnapshot.model_id == route.model_id,
                PricingSnapshot.effective_at <= effective_at,
            )
            .order_by(desc(PricingSnapshot.effective_at))
            .limit(1)
        )
        if pricing is None:
            raise RuntimeError("No pricing snapshot is configured for the selected route.")
        return pricing
