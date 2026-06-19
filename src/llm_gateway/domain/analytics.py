"""Privacy-safe aggregate analytics contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from llm_gateway.domain.base import ContractModel


class AnalyticsStatusCount(ContractModel):
    status: Annotated[str, Field(min_length=1, max_length=32)]
    count: Annotated[int, Field(ge=0)]


class AnalyticsAttemptSummary(ContractModel):
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    model: Annotated[str, Field(min_length=1, max_length=255)]
    status: Annotated[str, Field(min_length=1, max_length=32)]
    count: Annotated[int, Field(ge=0)]


class AnalyticsUsageTotals(ContractModel):
    usage_records: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]


class AnalyticsCostTotal(ContractModel):
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    amount: Annotated[Decimal, Field(ge=0)]


class AnalyticsReconciliationSummary(ContractModel):
    succeeded_requests_without_usage: Annotated[int, Field(ge=0)]
    usage_rows_without_succeeded_attempt: Annotated[int, Field(ge=0)]
    duplicate_charge_violations: Annotated[int, Field(ge=0)]


class AnalyticsUsageSummary(ContractModel):
    request_statuses: list[AnalyticsStatusCount]
    provider_model_attempts: list[AnalyticsAttemptSummary]
    usage: AnalyticsUsageTotals
    costs: list[AnalyticsCostTotal]
    reconciliation: AnalyticsReconciliationSummary
