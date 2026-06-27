"""Stable offline report schema for deterministic evaluation runs."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from llm_gateway.evaluation.scoring import (
    AutoRouteComparison,
    CaseScore,
    PassFailThresholds,
    RunAggregate,
    ScoreResult,
)

REPORT_SCHEMA_VERSION: Literal["llm-gateway.evaluation.v1"] = "llm-gateway.evaluation.v1"


def build_evaluation_report(
    *,
    baseline_cases: tuple[CaseScore, ...],
    auto_cases: tuple[CaseScore, ...],
    comparison: AutoRouteComparison,
) -> dict[str, Any]:
    """Build a deterministic, JSON-serializable report dictionary."""

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "thresholds": _thresholds_to_dict(comparison.thresholds),
        "summary": {
            "passed": comparison.passed,
            "quality_preserved_or_improved": comparison.quality_preserved_or_improved,
            "efficiency_improved": comparison.efficiency_improved,
            "cost_improved": comparison.cost_improved,
            "latency_improved": comparison.latency_improved,
            "case_regressions": list(comparison.case_regressions),
            "missing_case_ids": list(comparison.missing_case_ids),
        },
        "baseline": _aggregate_to_dict(comparison.baseline),
        "auto": _aggregate_to_dict(comparison.auto),
        "cases": {
            "baseline": [
                _case_to_dict(case)
                for case in sorted(baseline_cases, key=lambda item: item.case_id)
            ],
            "auto": [
                _case_to_dict(case) for case in sorted(auto_cases, key=lambda item: item.case_id)
            ],
        },
    }


def _thresholds_to_dict(thresholds: PassFailThresholds) -> dict[str, Any]:
    return {
        "minimum_case_quality_score": _decimal_to_string(thresholds.minimum_case_quality_score),
        "minimum_average_quality_score": _decimal_to_string(
            thresholds.minimum_average_quality_score
        ),
        "max_failed_cases": thresholds.max_failed_cases,
        "require_per_case_quality_preserved": thresholds.require_per_case_quality_preserved,
    }


def _aggregate_to_dict(aggregate: RunAggregate) -> dict[str, Any]:
    return {
        "cases": aggregate.cases,
        "passed_cases": aggregate.passed_cases,
        "failed_cases": aggregate.failed_cases,
        "average_quality_score": _decimal_to_string(aggregate.average_quality_score),
        "total_cost": _decimal_to_string(aggregate.total_cost),
        "average_cost": _decimal_to_string(aggregate.average_cost),
        "average_latency_ms": _decimal_to_string(aggregate.average_latency_ms),
        "min_latency_ms": aggregate.min_latency_ms,
        "max_latency_ms": aggregate.max_latency_ms,
    }


def _case_to_dict(case: CaseScore) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "route": case.route,
        "passed": case.passed,
        "quality_score": _decimal_to_string(case.quality_score),
        "cost": _decimal_to_string(case.cost),
        "latency_ms": case.latency_ms,
        "results": [_result_to_dict(result) for result in case.results],
    }


def _result_to_dict(result: ScoreResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "passed": result.passed,
        "score": _decimal_to_string(result.score),
        "reason": result.reason,
    }


def _decimal_to_string(value: Decimal) -> str:
    return format(value, "f")
