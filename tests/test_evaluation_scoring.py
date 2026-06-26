from __future__ import annotations

from decimal import Decimal

from llm_gateway.evaluation.reporting import build_evaluation_report
from llm_gateway.evaluation.scoring import (
    FormatExpectation,
    RequiredConcept,
    ResponseExpectation,
    SafetyExpectation,
    aggregate_case_scores,
    compare_auto_to_baseline,
    score_case,
    score_constrained_format,
    score_exact_match,
    score_required_concepts,
    score_safety_expectation,
)


def test_normalized_exact_match_ignores_case_whitespace_and_punctuation() -> None:
    result = score_exact_match("  The ANSWER: Paris! ", "the answer paris")

    assert result.passed is True
    assert result.score == Decimal("1")
    assert score_exact_match("Paris", "London").passed is False


def test_required_concepts_use_alternatives_and_token_boundaries() -> None:
    result = score_required_concepts(
        "The gateway uses Redis caching and records estimated cost.",
        (
            RequiredConcept(name="cache", alternatives=("cache", "caching")),
            RequiredConcept(name="cost", alternatives=("estimated cost",)),
        ),
    )

    assert result.passed is True
    assert result.score == Decimal("1")

    missing = score_required_concepts(
        "The model is costly but does not mention the cache concept.",
        (RequiredConcept(name="cost", alternatives=("cost",)),),
    )
    assert missing.passed is False
    assert missing.score == Decimal("0")


def test_constrained_format_checks_json_shape_and_required_keys() -> None:
    expectation = FormatExpectation(
        kind="json_object",
        required_json_keys=("answer", "sources"),
        allow_extra_json_keys=False,
    )

    assert score_constrained_format('{"answer":"ok","sources":[]}', expectation).passed is True
    extra = score_constrained_format(
        '{"answer":"ok","sources":[],"debug":"raw prompt"}',
        expectation,
    )
    assert extra.passed is False
    assert "unexpected JSON keys" in extra.reason
    assert score_constrained_format("prefix {\"answer\":\"ok\"}", expectation).passed is False


def test_safety_expectation_is_binary_and_does_not_call_judges() -> None:
    safe_refusal = score_safety_expectation(
        "I cannot assist with that request, but I can help with safer alternatives.",
        SafetyExpectation(must_refuse=True, disallowed_phrases=("bypass authentication",)),
    )

    assert safe_refusal.passed is True
    unsafe = score_safety_expectation(
        "Here is how to bypass authentication.",
        SafetyExpectation(must_refuse=True, disallowed_phrases=("bypass authentication",)),
    )
    assert unsafe.passed is False
    assert unsafe.score == Decimal("0")


def test_case_scoring_aggregates_quality_cost_and_latency() -> None:
    case = score_case(
        case_id="case-1",
        route="auto",
        actual='{"answer":"Paris","sources":[]}',
        expectation=ResponseExpectation(
            exact_answer="Paris",
            required_concepts=(RequiredConcept(name="capital", alternatives=("Paris",)),),
            format=FormatExpectation(kind="json_object", required_json_keys=("answer", "sources")),
        ),
        cost=Decimal("0.0100"),
        latency_ms=125,
        minimum_quality_score=Decimal("0.65"),
    )

    assert case.passed is False
    assert case.quality_score == Decimal("2") / Decimal("3")

    aggregate = aggregate_case_scores((case,))
    assert aggregate.cases == 1
    assert aggregate.failed_cases == 1
    assert aggregate.total_cost == Decimal("0.0100")
    assert aggregate.average_latency_ms == Decimal("125")


def test_auto_route_passes_only_when_quality_is_preserved_and_efficiency_improves() -> None:
    expectation = ResponseExpectation(exact_answer="Paris")
    baseline = (
        score_case(
            case_id="capital",
            route="baseline",
            actual="Paris",
            expectation=expectation,
            cost=Decimal("0.0200"),
            latency_ms=180,
        ),
    )
    cheaper_auto = (
        score_case(
            case_id="capital",
            route="auto",
            actual="Paris",
            expectation=expectation,
            cost=Decimal("0.0100"),
            latency_ms=240,
        ),
    )
    lower_quality_auto = (
        score_case(
            case_id="capital",
            route="auto",
            actual="London",
            expectation=expectation,
            cost=Decimal("0.0010"),
            latency_ms=50,
        ),
    )
    same_efficiency_auto = (
        score_case(
            case_id="capital",
            route="auto",
            actual="Paris",
            expectation=expectation,
            cost=Decimal("0.0200"),
            latency_ms=180,
        ),
    )

    passing = compare_auto_to_baseline(baseline_cases=baseline, auto_cases=cheaper_auto)
    assert passing.passed is True
    assert passing.cost_improved is True
    assert passing.latency_improved is False

    quality_regression = compare_auto_to_baseline(
        baseline_cases=baseline,
        auto_cases=lower_quality_auto,
    )
    assert quality_regression.passed is False
    assert quality_regression.quality_preserved_or_improved is False
    assert quality_regression.case_regressions == ("capital",)

    no_efficiency_gain = compare_auto_to_baseline(
        baseline_cases=baseline,
        auto_cases=same_efficiency_auto,
    )
    assert no_efficiency_gain.passed is False
    assert no_efficiency_gain.efficiency_improved is False


def test_evaluation_report_schema_is_deterministic_and_json_ready() -> None:
    expectation = ResponseExpectation(exact_answer="Paris")
    baseline = (
        score_case(
            case_id="capital",
            route="baseline",
            actual="Paris",
            expectation=expectation,
            cost=Decimal("0.0200"),
            latency_ms=180,
        ),
    )
    auto = (
        score_case(
            case_id="capital",
            route="auto",
            actual="Paris",
            expectation=expectation,
            cost=Decimal("0.0100"),
            latency_ms=160,
        ),
    )
    comparison = compare_auto_to_baseline(baseline_cases=baseline, auto_cases=auto)

    report = build_evaluation_report(
        baseline_cases=baseline,
        auto_cases=auto,
        comparison=comparison,
    )

    assert report == {
        "schema_version": "llm-gateway.evaluation.v1",
        "thresholds": {
            "minimum_case_quality_score": "1",
            "minimum_average_quality_score": "1",
            "max_failed_cases": 0,
            "require_per_case_quality_preserved": True,
        },
        "summary": {
            "passed": True,
            "quality_preserved_or_improved": True,
            "efficiency_improved": True,
            "cost_improved": True,
            "latency_improved": True,
            "case_regressions": [],
            "missing_case_ids": [],
        },
        "baseline": {
            "cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
            "average_quality_score": "1",
            "total_cost": "0.0200",
            "average_cost": "0.0200",
            "average_latency_ms": "180",
            "min_latency_ms": 180,
            "max_latency_ms": 180,
        },
        "auto": {
            "cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
            "average_quality_score": "1",
            "total_cost": "0.0100",
            "average_cost": "0.0100",
            "average_latency_ms": "160",
            "min_latency_ms": 160,
            "max_latency_ms": 160,
        },
        "cases": {
            "baseline": [
                {
                    "case_id": "capital",
                    "route": "baseline",
                    "passed": True,
                    "quality_score": "1",
                    "cost": "0.0200",
                    "latency_ms": 180,
                    "results": [
                        {
                            "name": "exact_match",
                            "passed": True,
                            "score": "1",
                            "reason": "normalized answers match",
                        }
                    ],
                }
            ],
            "auto": [
                {
                    "case_id": "capital",
                    "route": "auto",
                    "passed": True,
                    "quality_score": "1",
                    "cost": "0.0100",
                    "latency_ms": 160,
                    "results": [
                        {
                            "name": "exact_match",
                            "passed": True,
                            "score": "1",
                            "reason": "normalized answers match",
                        }
                    ],
                }
            ],
        },
    }
