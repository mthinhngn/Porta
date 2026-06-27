from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from llm_gateway.evaluation.benchmark import (
    DEFAULT_FIXTURES,
    BenchmarkBudgetExceeded,
    BenchmarkConfig,
    PaidLiveBenchmarkRefused,
    run_benchmark,
)


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value)


def _case_list(value: object) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], value)


def test_local_benchmark_compares_baseline_and_candidate() -> None:
    report = run_benchmark()
    controls = _mapping(report["controls"])
    summary = _mapping(report["summary"])
    baseline_summary = _mapping(summary["baseline"])
    candidate_summary = _mapping(summary["candidate_auto"])

    assert controls["mode"] == "local"
    assert controls["paid_live_enabled"] is False
    assert controls["request_count"] == len(DEFAULT_FIXTURES) * 2
    assert baseline_summary["case_count"] == len(DEFAULT_FIXTURES)
    assert candidate_summary["case_count"] == len(DEFAULT_FIXTURES)
    assert candidate_summary["total_cost_usd"] == "0.0000000000"
    assert baseline_summary["total_cost_usd"] != "0.0000000000"
    for case in _case_list(report["cases"]):
        results = _mapping(case["results"])
        candidate = _mapping(results["candidate_auto"])
        baseline = _mapping(results["baseline"])
        assert Decimal(candidate["score"]) >= Decimal(baseline["score"])


def test_report_generation_writes_deterministic_json(tmp_path: Path) -> None:
    report_path = tmp_path / "phase4-benchmark.json"
    report = run_benchmark(BenchmarkConfig(report_path=report_path))

    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written == report
    assert written["schema_version"] == "phase4-benchmark-report-v1"
    assert written["report_id"]


def test_paid_live_refuses_without_explicit_controls() -> None:
    with pytest.raises(PaidLiveBenchmarkRefused):
        run_benchmark(
            BenchmarkConfig(
                mode="paid-live",
                allow_paid_live=False,
                paid_live_env_value=None,
                max_requests=20,
                max_spend_usd=Decimal("0.01"),
            )
        )


def test_paid_live_budget_cap_stops_projected_spend() -> None:
    with pytest.raises(BenchmarkBudgetExceeded):
        run_benchmark(
            BenchmarkConfig(
                mode="paid-live",
                allow_paid_live=True,
                paid_live_env_value="1",
                max_requests=20,
                max_spend_usd=Decimal("0.000001"),
            )
        )


def test_request_cap_stops_execution() -> None:
    with pytest.raises(BenchmarkBudgetExceeded):
        run_benchmark(BenchmarkConfig(max_requests=1))


def test_local_report_shape_is_reproducible() -> None:
    first = run_benchmark()
    second = run_benchmark()

    assert first == second
    assert list(first) == [
        "schema_version",
        "benchmark_version",
        "task_routing_version",
        "controls",
        "policies",
        "summary",
        "cases",
        "report_id",
    ]
    assert [case["case_id"] for case in _case_list(first["cases"])] == sorted(
        case.case_id for case in DEFAULT_FIXTURES
    )
