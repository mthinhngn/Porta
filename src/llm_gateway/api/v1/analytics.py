"""Authenticated privacy-safe usage analytics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import Select, and_, exists, func, literal, or_, select
from sqlalchemy.orm import Session, sessionmaker

from llm_gateway.core.auth import authenticated_actor
from llm_gateway.core.errors import ApiError
from llm_gateway.domain import (
    AnalyticsAttemptSummary,
    AnalyticsCostTotal,
    AnalyticsReconciliationSummary,
    AnalyticsStatusCount,
    AnalyticsUsageSummary,
    AnalyticsUsageTotals,
)
from llm_gateway.persistence import GatewayRequest, Model, Provider, ProviderAttempt, UsageRecord

router = APIRouter(prefix="/analytics", tags=["analytics"])


@dataclass(frozen=True, slots=True)
class AnalyticsFilters:
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None


def _session_factory(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not isinstance(factory, sessionmaker):
        raise ApiError(
            message="Analytics service is not configured.",
            type="server_error",
            status_code=503,
            code="service_unavailable",
        )
    return factory


def _request_filter_clauses(filters: AnalyticsFilters) -> list[Any]:
    clauses: list[Any] = []
    if filters.status is not None:
        clauses.append(GatewayRequest.status == filters.status)
    if filters.model is not None:
        clauses.append(GatewayRequest.requested_model == filters.model)
    if filters.from_time is not None:
        clauses.append(GatewayRequest.created_at >= filters.from_time)
    if filters.to_time is not None:
        clauses.append(GatewayRequest.created_at <= filters.to_time)
    if filters.provider is not None:
        clauses.append(
            exists(
                select(literal(1))
                .select_from(ProviderAttempt)
                .join(Provider, Provider.id == ProviderAttempt.provider_id)
                .where(
                    ProviderAttempt.gateway_request_id == GatewayRequest.id,
                    Provider.name == filters.provider,
                )
            )
        )
    return clauses


def _apply_request_filters(
    statement: Select[Any],
    filters: AnalyticsFilters,
) -> Select[Any]:
    clauses = _request_filter_clauses(filters)
    if clauses:
        return statement.where(and_(*clauses))
    return statement


def _attempt_filter_clauses(filters: AnalyticsFilters) -> list[Any]:
    clauses: list[Any] = []
    if filters.provider is not None:
        clauses.append(Provider.name == filters.provider)
    if filters.model is not None:
        clauses.append(Model.gateway_name == filters.model)
    if filters.status is not None:
        clauses.append(GatewayRequest.status == filters.status)
    if filters.from_time is not None:
        clauses.append(GatewayRequest.created_at >= filters.from_time)
    if filters.to_time is not None:
        clauses.append(GatewayRequest.created_at <= filters.to_time)
    return clauses


def _apply_attempt_filters(
    statement: Select[Any],
    filters: AnalyticsFilters,
) -> Select[Any]:
    clauses = _attempt_filter_clauses(filters)
    if clauses:
        return statement.where(and_(*clauses))
    return statement


def _usage_filter_clauses(filters: AnalyticsFilters) -> list[Any]:
    clauses = _attempt_filter_clauses(filters)
    if filters.status is None:
        return clauses
    return clauses


def _apply_usage_filters(
    statement: Select[Any],
    filters: AnalyticsFilters,
) -> Select[Any]:
    clauses = _usage_filter_clauses(filters)
    if clauses:
        return statement.where(and_(*clauses))
    return statement


def _int_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, float | str | bytes | bytearray):
        return int(value)
    return 0


def _decimal_value(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _summary_from_database(
    session_factory: sessionmaker[Session],
    filters: AnalyticsFilters,
) -> AnalyticsUsageSummary:
    with session_factory() as session:
        status_statement = _apply_request_filters(
            select(GatewayRequest.status, func.count(GatewayRequest.id))
            .select_from(GatewayRequest)
            .group_by(GatewayRequest.status)
            .order_by(GatewayRequest.status),
            filters,
        )
        request_statuses = [
            AnalyticsStatusCount(status=str(status), count=_int_value(count))
            for status, count in session.execute(status_statement)
        ]

        attempt_statement = _apply_attempt_filters(
            select(Provider.name, Model.gateway_name, ProviderAttempt.status, func.count())
            .select_from(ProviderAttempt)
            .join(GatewayRequest, GatewayRequest.id == ProviderAttempt.gateway_request_id)
            .join(Provider, Provider.id == ProviderAttempt.provider_id)
            .join(
                Model,
                and_(
                    Model.id == ProviderAttempt.model_id,
                    Model.provider_id == ProviderAttempt.provider_id,
                ),
            )
            .group_by(Provider.name, Model.gateway_name, ProviderAttempt.status)
            .order_by(Provider.name, Model.gateway_name, ProviderAttempt.status),
            filters,
        )
        provider_model_attempts = [
            AnalyticsAttemptSummary(
                provider=str(provider),
                model=str(model),
                status=str(status),
                count=_int_value(count),
            )
            for provider, model, status, count in session.execute(attempt_statement)
        ]

        usage_statement = _apply_usage_filters(
            select(
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cached_input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0),
            )
            .select_from(UsageRecord)
            .join(GatewayRequest, GatewayRequest.id == UsageRecord.gateway_request_id)
            .outerjoin(
                ProviderAttempt,
                and_(
                    ProviderAttempt.id == UsageRecord.provider_attempt_id,
                    ProviderAttempt.gateway_request_id == UsageRecord.gateway_request_id,
                ),
            )
            .outerjoin(Provider, Provider.id == ProviderAttempt.provider_id)
            .outerjoin(
                Model,
                and_(
                    Model.id == ProviderAttempt.model_id,
                    Model.provider_id == ProviderAttempt.provider_id,
                ),
            ),
            filters,
        )
        usage_row = session.execute(usage_statement).one()
        usage = AnalyticsUsageTotals(
            usage_records=_int_value(usage_row[0]),
            input_tokens=_int_value(usage_row[1]),
            cached_input_tokens=_int_value(usage_row[2]),
            output_tokens=_int_value(usage_row[3]),
            total_tokens=_int_value(usage_row[4]),
        )

        cost_statement = _apply_usage_filters(
            select(UsageRecord.currency, func.coalesce(func.sum(UsageRecord.estimated_cost), 0))
            .select_from(UsageRecord)
            .join(GatewayRequest, GatewayRequest.id == UsageRecord.gateway_request_id)
            .outerjoin(
                ProviderAttempt,
                and_(
                    ProviderAttempt.id == UsageRecord.provider_attempt_id,
                    ProviderAttempt.gateway_request_id == UsageRecord.gateway_request_id,
                ),
            )
            .outerjoin(Provider, Provider.id == ProviderAttempt.provider_id)
            .outerjoin(
                Model,
                and_(
                    Model.id == ProviderAttempt.model_id,
                    Model.provider_id == ProviderAttempt.provider_id,
                ),
            )
            .where(UsageRecord.currency.is_not(None))
            .group_by(UsageRecord.currency)
            .order_by(UsageRecord.currency),
            filters,
        )
        costs = [
            AnalyticsCostTotal(currency=str(currency), amount=_decimal_value(amount))
            for currency, amount in session.execute(cost_statement)
        ]

        succeeded_without_usage_statement = _apply_request_filters(
            select(func.count(GatewayRequest.id)).where(
                GatewayRequest.status == "succeeded",
                ~exists(
                    select(literal(1))
                    .select_from(UsageRecord)
                    .where(UsageRecord.gateway_request_id == GatewayRequest.id)
                ),
            ),
            filters,
        )
        succeeded_without_usage = _int_value(session.scalar(succeeded_without_usage_statement))

        usage_without_succeeded_attempt_statement = _apply_usage_filters(
            select(func.count(UsageRecord.id))
            .select_from(UsageRecord)
            .join(GatewayRequest, GatewayRequest.id == UsageRecord.gateway_request_id)
            .outerjoin(
                ProviderAttempt,
                and_(
                    ProviderAttempt.id == UsageRecord.provider_attempt_id,
                    ProviderAttempt.gateway_request_id == UsageRecord.gateway_request_id,
                ),
            )
            .outerjoin(Provider, Provider.id == ProviderAttempt.provider_id)
            .outerjoin(
                Model,
                and_(
                    Model.id == ProviderAttempt.model_id,
                    Model.provider_id == ProviderAttempt.provider_id,
                ),
            )
            .where(or_(ProviderAttempt.id.is_(None), ProviderAttempt.status != "succeeded")),
            filters,
        )
        usage_without_succeeded_attempt = _int_value(
            session.scalar(usage_without_succeeded_attempt_statement)
        )

        duplicate_subquery = (
            select(UsageRecord.provider_attempt_id)
            .where(UsageRecord.provider_attempt_id.is_not(None))
            .group_by(UsageRecord.provider_attempt_id)
            .having(func.count(UsageRecord.id) > 1)
            .subquery()
        )
        duplicate_charge_violations = _int_value(
            session.scalar(select(func.count()).select_from(duplicate_subquery))
        )

    return AnalyticsUsageSummary(
        request_statuses=request_statuses,
        provider_model_attempts=provider_model_attempts,
        usage=usage,
        costs=costs,
        reconciliation=AnalyticsReconciliationSummary(
            succeeded_requests_without_usage=succeeded_without_usage,
            usage_rows_without_succeeded_attempt=usage_without_succeeded_attempt,
            duplicate_charge_violations=duplicate_charge_violations,
        ),
    )


@router.get("/usage/summary", response_model=AnalyticsUsageSummary)
async def usage_summary(
    request: Request,
    provider: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    model: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    status: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> AnalyticsUsageSummary:
    actor = authenticated_actor(request)
    if not actor.is_admin:
        raise ApiError(
            message="Analytics access is not allowed.",
            type="invalid_request_error",
            status_code=403,
            code="analytics_access_denied",
        )
    filters = AnalyticsFilters(
        provider=provider,
        model=model,
        status=status,
        from_time=from_time,
        to_time=to_time,
    )
    return await asyncio.to_thread(_summary_from_database, _session_factory(request), filters)
