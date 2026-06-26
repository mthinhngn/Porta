"""Deterministic Phase 4 routing benchmark helpers.

The default runner is intentionally local-only: it uses fixed fixtures and
synthetic provider outcomes so CI and laptops can compare routing policies
without API keys, network calls, or paid provider spend.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from llm_gateway.core.task_routing import TASK_ROUTING_VERSION, TaskKind, classify_task

BenchmarkMode = Literal["local", "paid-live"]
PolicyName = Literal["baseline", "candidate_auto"]
ProviderName = Literal["openai", "llama", "qwen"]

BENCHMARK_SCHEMA_VERSION = "phase4-benchmark-report-v1"
BENCHMARK_VERSION = "phase4-local-v1"
DEFAULT_MAX_REQUESTS = 20
DEFAULT_MAX_SPEND_USD = Decimal("0.00")
PAID_LIVE_ENV_FLAG = "LLM_GATEWAY_PHASE4_PAID_LIVE"
_MONEY_QUANT = Decimal("0.0000000001")


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    expected_task: TaskKind
    minimum_score: Decimal
    provider_scores: Mapping[ProviderName, Decimal]
    provider_latency_ms: Mapping[ProviderName, int]
    provider_cost_usd: Mapping[ProviderName, Decimal]
    expected_local_provider: ProviderName


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    mode: BenchmarkMode = "local"
    allow_paid_live: bool = False
    paid_live_env_value: str | None = None
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_spend_usd: Decimal = DEFAULT_MAX_SPEND_USD
    report_path: Path | None = None


class BenchmarkError(ValueError):
    """Base error for benchmark preflight and runtime guard failures."""


class PaidLiveBenchmarkRefused(BenchmarkError):
    """Raised when paid live execution was requested without explicit opt-in."""


class BenchmarkBudgetExceeded(BenchmarkError):
    """Raised when request or spend limits would be exceeded."""


DEFAULT_FIXTURES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(
        case_id="general-summary",
        prompt="Summarize this paragraph in one sentence.",
        expected_task="general",
        minimum_score=Decimal("0.80"),
        provider_scores={
            "openai": Decimal("0.92"),
            "llama": Decimal("0.93"),
            "qwen": Decimal("0.74"),
        },
        provider_latency_ms={"openai": 620, "llama": 180, "qwen": 230},
        provider_cost_usd={
            "openai": Decimal("0.0000060000"),
            "llama": Decimal("0"),
            "qwen": Decimal("0"),
        },
        expected_local_provider="llama",
    ),
    BenchmarkCase(
        case_id="code-debug",
        prompt="Debug this Python function and explain the fix.",
        expected_task="coding",
        minimum_score=Decimal("0.80"),
        provider_scores={
            "openai": Decimal("0.93"),
            "llama": Decimal("0.72"),
            "qwen": Decimal("0.94"),
        },
        provider_latency_ms={"openai": 700, "llama": 210, "qwen": 260},
        provider_cost_usd={
            "openai": Decimal("0.0000074000"),
            "llama": Decimal("0"),
            "qwen": Decimal("0"),
        },
        expected_local_provider="qwen",
    ),
    BenchmarkCase(
        case_id="sql-routing",
        prompt="Write a SQL query to count active users by month.",
        expected_task="coding",
        minimum_score=Decimal("0.80"),
        provider_scores={
            "openai": Decimal("0.91"),
            "llama": Decimal("0.70"),
            "qwen": Decimal("0.92"),
        },
        provider_latency_ms={"openai": 680, "llama": 205, "qwen": 250},
        provider_cost_usd={
            "openai": Decimal("0.0000068000"),
            "llama": Decimal("0"),
            "qwen": Decimal("0"),
        },
        expected_local_provider="qwen",
    ),
    BenchmarkCase(
        case_id="friendly-copy",
        prompt="Write a friendly status update for a project teammate.",
        expected_task="general",
        minimum_score=Decimal("0.80"),
        provider_scores={
            "openai": Decimal("0.90"),
            "llama": Decimal("0.91"),
            "qwen": Decimal("0.76"),
        },
        provider_latency_ms={"openai": 590, "llama": 175, "qwen": 225},
        provider_cost_usd={
            "openai": Decimal("0.0000052000"),
            "llama": Decimal("0"),
            "qwen": Decimal("0"),
        },
        expected_local_provider="llama",
    ),
)


def run_benchmark(
    config: BenchmarkConfig | None = None,
    *,
    fixtures: Iterable[BenchmarkCase] = DEFAULT_FIXTURES,
) -> dict[str, object]:
    resolved_config = config or BenchmarkConfig()
    fixture_tuple = tuple(fixtures)
    _validate_fixture_classification(fixture_tuple)
    _preflight_controls(resolved_config, fixture_tuple)

    case_results = [
        _case_report(case)
        for case in sorted(fixture_tuple, key=lambda item: item.case_id)
    ]
    report = _report_from_cases(resolved_config, case_results)

    if resolved_config.report_path is not None:
        write_report(report, resolved_config.report_path)
    return report


def write_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_fixture_classification(fixtures: tuple[BenchmarkCase, ...]) -> None:
    for case in fixtures:
        actual_task = classify_task(case.prompt)
        if actual_task != case.expected_task:
            raise BenchmarkError(
                f"fixture {case.case_id!r} expected {case.expected_task!r}, got {actual_task!r}"
            )


def _preflight_controls(config: BenchmarkConfig, fixtures: tuple[BenchmarkCase, ...]) -> None:
    if config.max_requests < 1:
        raise BenchmarkBudgetExceeded("max_requests must be at least 1")
    if config.max_spend_usd < 0:
        raise BenchmarkBudgetExceeded("max_spend_usd must not be negative")

    projected_requests = len(fixtures) * 2
    if projected_requests > config.max_requests:
        raise BenchmarkBudgetExceeded(
            f"benchmark requires {projected_requests} requests, max_requests={config.max_requests}"
        )

    if config.mode == "paid-live":
        if not config.allow_paid_live or config.paid_live_env_value != "1":
            raise PaidLiveBenchmarkRefused(
                "paid live benchmark requires allow_paid_live=True and "
                f"{PAID_LIVE_ENV_FLAG}=1"
            )
        projected_spend = _projected_paid_spend(fixtures)
        if projected_spend > config.max_spend_usd:
            raise BenchmarkBudgetExceeded(
                f"projected paid spend USD {_money(projected_spend)} exceeds "
                f"max_spend_usd={_money(config.max_spend_usd)}"
            )


def _projected_paid_spend(fixtures: tuple[BenchmarkCase, ...]) -> Decimal:
    return sum(
        (
            case.provider_cost_usd["openai"]
            + case.provider_cost_usd[_candidate_provider(case)]
            for case in fixtures
        ),
        Decimal("0"),
    )


def _case_report(case: BenchmarkCase) -> dict[str, object]:
    baseline = _result_for_policy(case, "baseline")
    candidate = _result_for_policy(case, "candidate_auto")
    return {
        "case_id": case.case_id,
        "task": case.expected_task,
        "prompt_hash": _hash(case.prompt),
        "results": {
            "baseline": baseline,
            "candidate_auto": candidate,
        },
        "delta": {
            "score": _money(_decimal(candidate["score"]) - _decimal(baseline["score"])),
            "latency_ms": _int(candidate["latency_ms"]) - _int(baseline["latency_ms"]),
            "cost_usd": _money(
                _decimal(candidate["cost_usd"]) - _decimal(baseline["cost_usd"])
            ),
        },
    }


def _result_for_policy(case: BenchmarkCase, policy: PolicyName) -> dict[str, object]:
    provider = _baseline_provider(case) if policy == "baseline" else _candidate_provider(case)
    score = case.provider_scores[provider]
    return {
        "provider": provider,
        "score": _money(score),
        "passed": score >= case.minimum_score,
        "latency_ms": case.provider_latency_ms[provider],
        "cost_usd": _money(case.provider_cost_usd[provider]),
        "output_hash": _hash(f"{BENCHMARK_VERSION}:{case.case_id}:{policy}:{provider}"),
    }


def _baseline_provider(case: BenchmarkCase) -> ProviderName:
    return "openai"


def _candidate_provider(case: BenchmarkCase) -> ProviderName:
    local_provider = case.expected_local_provider
    if case.provider_scores[local_provider] >= case.minimum_score:
        return local_provider
    return "openai"


def _report_from_cases(
    config: BenchmarkConfig,
    case_results: list[dict[str, object]],
) -> dict[str, object]:
    baseline = _summary(case_results, "baseline")
    candidate = _summary(case_results, "candidate_auto")
    controls: dict[str, object] = {
        "mode": config.mode,
        "paid_live_enabled": config.mode == "paid-live",
        "max_requests": config.max_requests,
        "max_spend_usd": _money(config.max_spend_usd),
        "request_count": len(case_results) * 2,
    }
    report: dict[str, object] = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "task_routing_version": TASK_ROUTING_VERSION,
        "controls": controls,
        "policies": {
            "baseline": {
                "name": "baseline",
                "description": "Current configured routing order: openai first, then task local.",
            },
            "candidate_auto": {
                "name": "candidate_auto",
                "description": "Local-first auto policy when fixture quality clears the floor.",
            },
        },
        "summary": {
            "baseline": baseline,
            "candidate_auto": candidate,
            "delta": {
                "pass_rate": _money(
                    _decimal(candidate["pass_rate"]) - _decimal(baseline["pass_rate"])
                ),
                "total_cost_usd": _money(
                    _decimal(candidate["total_cost_usd"])
                    - _decimal(baseline["total_cost_usd"])
                ),
                "mean_latency_ms": _int(candidate["mean_latency_ms"])
                - _int(baseline["mean_latency_ms"]),
            },
        },
        "cases": case_results,
    }
    report["report_id"] = _hash(json.dumps(report, sort_keys=True, default=str))
    return report


def _summary(case_results: list[dict[str, object]], policy: PolicyName) -> dict[str, object]:
    results: list[Mapping[str, object]] = []
    for case in case_results:
        policy_results = case["results"]
        if not isinstance(policy_results, Mapping):
            raise BenchmarkError("case results must be mappings")
        result = policy_results[policy]
        if not isinstance(result, Mapping):
            raise BenchmarkError("policy results must be mappings")
        results.append(result)
    total = len(results)
    pass_count = sum(1 for result in results if _bool(result["passed"]))
    total_cost = sum((_decimal(result["cost_usd"]) for result in results), Decimal("0"))
    total_latency = sum(_int(result["latency_ms"]) for result in results)
    mean_latency = round(total_latency / total) if total else 0
    return {
        "case_count": total,
        "pass_count": pass_count,
        "pass_rate": _money(Decimal(pass_count) / Decimal(total) if total else Decimal("0")),
        "total_cost_usd": _money(total_cost),
        "mean_latency_ms": mean_latency,
    }


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _money(value: Decimal) -> str:
    return format(value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP), "f")


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkError(f"expected integer metric, got {value!r}")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkError(f"expected boolean metric, got {value!r}")
    return value
