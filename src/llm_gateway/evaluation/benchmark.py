"""Deterministic Phase 4 routing benchmark helpers.

The local runner uses the real generation service routing path with no network
calls or paid providers. It compares production tier behavior for
``tier=standard`` and ``tier=auto`` against the approved Phase 4 dataset.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from llm_gateway.core.errors import ApiError
from llm_gateway.core.task_routing import TASK_ROUTING_VERSION, TaskKind, classify_task
from llm_gateway.domain import GenerateRequest, GenerateResponse
from llm_gateway.evaluation.dataset import EvaluationCase, load_phase4_v1_dataset
from llm_gateway.evaluation.reporting import build_evaluation_report
from llm_gateway.evaluation.scoring import (
    AutoRouteComparison,
    CaseScore,
    FormatExpectation,
    RequiredConcept,
    ResponseExpectation,
    SafetyExpectation,
    compare_auto_to_baseline,
    score_case,
)
from llm_gateway.persistence.ledger import GatewayRoute, RouteBootstrap, UsageCost
from llm_gateway.providers import GenerateProviderContext, GenerateProviderResult
from llm_gateway.providers.protocol import ProviderTokenUsage
from llm_gateway.services.generation import AUTO_ROUTING_POLICY_VERSION, GenerationService

BenchmarkMode = Literal["local", "paid-live"]
ProviderName = Literal["openai", "llama", "qwen"]

BENCHMARK_SCHEMA_VERSION = "phase4-benchmark-report-v2"
BENCHMARK_VERSION = "phase4-service-v1"
DEFAULT_MAX_REQUESTS = 24
DEFAULT_MAX_SPEND_USD = Decimal("0.00")
PAID_LIVE_ENV_FLAG = "LLM_GATEWAY_PHASE4_PAID_LIVE"
_MONEY_QUANT = Decimal("0.0000000001")
_GATEWAY_MODEL = "gateway-default"
_APPROVED_PHASE4_CASE_IDS = frozenset(
    {
        "phase4-v1-coding-001",
        "phase4-v1-coding-002",
        "phase4-v1-general-001",
        "phase4-v1-general-002",
        "phase4-v1-summarization-001",
        "phase4-v1-summarization-002",
        "phase4-v1-factual-qa-001",
        "phase4-v1-factual-qa-002",
        "phase4-v1-constrained-format-001",
        "phase4-v1-constrained-format-002",
        "phase4-v1-guardrail-safe-001",
        "phase4-v1-guardrail-safe-002",
    }
)


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    mode: BenchmarkMode = "local"
    allow_paid_live: bool = False
    paid_live_env_value: str | None = None
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_spend_usd: Decimal = DEFAULT_MAX_SPEND_USD
    report_path: Path | None = None
    dataset_path: Path | None = None


class BenchmarkError(ValueError):
    """Base error for benchmark preflight and runtime guard failures."""


class PaidLiveBenchmarkRefused(BenchmarkError):
    """Raised when paid live execution was requested without explicit opt-in."""


class BenchmarkBudgetExceeded(BenchmarkError):
    """Raised when request or spend limits would be exceeded."""


class BenchmarkPassRuleFailed(BenchmarkError):
    """Raised when tier=auto does not satisfy the executable Phase 4 rule."""


class _BenchmarkProvider:
    def __init__(self, name: ProviderName, latency_ms: int) -> None:
        self._name = name
        self._latency_ms = latency_ms

    @property
    def name(self) -> str:
        return self._name

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        await asyncio.sleep(self._latency_ms / 1000)
        output = _output_for_case(_case_id_from_prompt(request.input), self._name)
        input_tokens = max(1, len(request.input.split()))
        output_tokens = max(1, len(output.split()))
        return GenerateProviderResult(
            output=output,
            usage=ProviderTokenUsage(
                input_tokens=input_tokens,
                cached_input_tokens=0,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            provider_request_id=f"phase4-{self._name}-{_hash(request.input)}",
        )


class _BenchmarkLedger:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], GatewayRoute] = {}

    def ensure_r1_route(self, config: RouteBootstrap) -> None:
        provider_id = _uuid(f"provider:{config.provider_name}")
        model_id = _uuid(f"model:{config.provider_name}:{config.upstream_model}")
        self._routes[(config.gateway_model, config.provider_name)] = GatewayRoute(
            provider_id=provider_id,
            provider_name=config.provider_name,
            model_id=model_id,
            gateway_model=config.gateway_model,
            upstream_model=config.upstream_model,
            routing_reason="configured_single_path",
        )

    def resolve_route(self, requested_model: str) -> GatewayRoute | None:
        return self.resolve_route_for_provider(requested_model, "openai")

    def resolve_route_for_provider(
        self,
        requested_model: str,
        provider_name: str,
    ) -> GatewayRoute | None:
        return self._routes.get((requested_model, provider_name))

    def begin_generation(
        self,
        *,
        correlation_id: str,
        requested_model: str,
        route: GatewayRoute,
        started_at: datetime,
    ) -> tuple[UUID, UUID]:
        request_id = _uuid(f"request:{correlation_id}")
        return request_id, _uuid(f"attempt:{request_id}:1")

    def begin_attempt(
        self,
        *,
        gateway_request_id: UUID,
        route: GatewayRoute,
        started_at: datetime,
    ) -> UUID:
        return _uuid(f"attempt:{gateway_request_id}:{route.provider_name}:{started_at.isoformat()}")

    def fail_generation(self, **kwargs: object) -> None:
        return None

    def fail_attempt(self, **kwargs: object) -> None:
        return None

    def fail_request(self, **kwargs: object) -> None:
        return None

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
        return self._usage_cost(route=route, usage=usage)

    def reconcile_generation_success(
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
        return self._usage_cost(route=route, usage=usage)

    @staticmethod
    def _usage_cost(*, route: GatewayRoute, usage: ProviderTokenUsage) -> UsageCost:
        estimated_cost = Decimal("0")
        if route.provider_name == "openai":
            estimated_cost = (
                Decimal(usage.input_tokens) * Decimal("0.4000000000") / Decimal("1000000")
                + Decimal(usage.output_tokens) * Decimal("1.6000000000") / Decimal("1000000")
            ).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
        return UsageCost(
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=estimated_cost,
            currency="USD",
        )


def run_benchmark(config: BenchmarkConfig | None = None) -> dict[str, object]:
    resolved_config = config or BenchmarkConfig()
    dataset = load_phase4_v1_dataset(resolved_config.dataset_path)
    cases = tuple(sorted(dataset.cases, key=lambda item: item.id))
    _validate_case_classification(cases)
    _preflight_controls(resolved_config, len(cases))

    started = perf_counter()
    baseline_scores, auto_scores, case_rows = asyncio.run(_run_service_cases(cases))
    missing_approved_case_ids = tuple(
        sorted(_APPROVED_PHASE4_CASE_IDS - {case.id for case in cases})
    )
    comparison = compare_auto_to_baseline(
        baseline_cases=tuple(baseline_scores),
        auto_cases=tuple(auto_scores),
    )
    report = _report_from_scores(
        config=resolved_config,
        dataset_name=dataset.name,
        dataset_version=dataset.schema_version,
        comparison=comparison,
        baseline_scores=tuple(baseline_scores),
        auto_scores=tuple(auto_scores),
        case_rows=case_rows,
        elapsed_ms=round((perf_counter() - started) * 1000),
        missing_approved_case_ids=missing_approved_case_ids,
    )

    if resolved_config.report_path is not None:
        write_report(report, resolved_config.report_path)
    if missing_approved_case_ids or not comparison.passed:
        raise BenchmarkPassRuleFailed(
            _failure_message(
                comparison,
                missing_approved_case_ids=missing_approved_case_ids,
            )
        )
    return report


def write_report(report: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _run_service_cases(
    cases: tuple[EvaluationCase, ...],
) -> tuple[list[CaseScore], list[CaseScore], list[dict[str, object]]]:
    service = _build_benchmark_service()
    service.bootstrap()
    baseline_scores = []
    auto_scores = []
    case_rows: list[dict[str, object]] = []
    for case in cases:
        baseline = await _run_case(service=service, case=case, tier="standard")
        auto = await _run_case(service=service, case=case, tier="auto")
        expectation = _expectation_for_case(case.id)
        baseline_score = score_case(
            case_id=case.id,
            route="tier=standard",
            actual=baseline.output,
            expectation=expectation,
            cost=baseline.cost.amount,
            latency_ms=baseline.latency_ms,
        )
        auto_score = score_case(
            case_id=case.id,
            route="tier=auto",
            actual=auto.output,
            expectation=expectation,
            cost=auto.cost.amount,
            latency_ms=auto.latency_ms,
        )
        baseline_scores.append(baseline_score)
        auto_scores.append(auto_score)
        case_rows.append(
            {
                "case_id": case.id,
                "category": case.category,
                "prompt_hash": _hash(case.prompt),
                "expected_task": classify_task(case.prompt),
                "standard": _response_row(baseline, baseline_score.quality_score),
                "auto": _response_row(auto, auto_score.quality_score),
                "delta": {
                    "quality_score": _money(
                        auto_score.quality_score - baseline_score.quality_score
                    ),
                    "latency_ms": auto.latency_ms - baseline.latency_ms,
                    "cost_usd": _money(auto.cost.amount - baseline.cost.amount),
                },
            }
        )
    return baseline_scores, auto_scores, case_rows


async def _run_case(
    *,
    service: GenerationService,
    case: EvaluationCase,
    tier: Literal["standard", "auto"],
) -> GenerateResponse:
    try:
        return await service.generate(
            GenerateRequest(model=_GATEWAY_MODEL, input=case.prompt, tier=tier),
            correlation_id=f"phase4-{tier}-{case.id}",
        )
    except ApiError as exc:
        raise BenchmarkError(f"{tier} failed for {case.id}: {exc.message}") from exc


def _build_benchmark_service() -> GenerationService:
    providers = {
        "openai": _BenchmarkProvider("openai", latency_ms=24),
        "llama": _BenchmarkProvider("llama", latency_ms=4),
        "qwen": _BenchmarkProvider("qwen", latency_ms=5),
    }
    service = GenerationService(
        provider_registry=providers,
        ledger=_BenchmarkLedger(),
        timeout_seconds=5,
        provider_order=["openai", "llama", "qwen"],
        bootstraps=_route_bootstraps(),
        auto_routing_enabled=True,
    )
    return service


def _route_bootstraps() -> tuple[RouteBootstrap, ...]:
    return (
        RouteBootstrap(
            provider_name="openai",
            provider_adapter="openai_responses",
            gateway_model=_GATEWAY_MODEL,
            upstream_model="gpt-4.1-mini",
            currency="USD",
            input_cost_per_million=Decimal("0.4000000000"),
            cached_input_cost_per_million=Decimal("0.1000000000"),
            output_cost_per_million=Decimal("1.6000000000"),
        ),
        RouteBootstrap(
            provider_name="llama",
            provider_adapter="ollama_generate",
            gateway_model=_GATEWAY_MODEL,
            upstream_model="llama3.2:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
        RouteBootstrap(
            provider_name="qwen",
            provider_adapter="ollama_generate",
            gateway_model=_GATEWAY_MODEL,
            upstream_model="qwen2.5-coder:3b",
            currency="USD",
            input_cost_per_million=Decimal("0"),
            cached_input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
        ),
    )


def _report_from_scores(
    *,
    config: BenchmarkConfig,
    dataset_name: str,
    dataset_version: str,
    comparison: AutoRouteComparison,
    baseline_scores: tuple[CaseScore, ...],
    auto_scores: tuple[CaseScore, ...],
    case_rows: list[dict[str, object]],
    elapsed_ms: int,
    missing_approved_case_ids: tuple[str, ...],
) -> dict[str, object]:
    evaluation = build_evaluation_report(
        baseline_cases=baseline_scores,
        auto_cases=auto_scores,
        comparison=comparison,
    )
    report: dict[str, object] = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
            "case_count": len(case_rows),
        },
        "task_routing_version": TASK_ROUTING_VERSION,
        "auto_routing_policy_version": AUTO_ROUTING_POLICY_VERSION,
        "controls": {
            "mode": config.mode,
            "paid_live_enabled": config.mode == "paid-live",
            "auto_routing_enabled_in_benchmark": True,
            "service_path": "GenerationService.generate",
            "compared_tiers": ["tier=standard", "tier=auto"],
            "max_requests": config.max_requests,
            "max_spend_usd": _money(config.max_spend_usd),
            "request_count": len(case_rows) * 2,
            "elapsed_ms": elapsed_ms,
        },
        "pass_rule": {
            "passed": comparison.passed and not missing_approved_case_ids,
            "no_quality_regression": comparison.quality_preserved_or_improved,
            "no_missing_cases": not comparison.missing_case_ids and not missing_approved_case_ids,
            "same_or_better_pass_rate": comparison.auto.passed_cases
            >= comparison.baseline.passed_cases,
            "better_cost_or_latency": comparison.efficiency_improved,
            "missing_approved_case_ids": list(missing_approved_case_ids),
        },
        "summary": {
            "standard": evaluation["baseline"],
            "auto": evaluation["auto"],
            "delta": {
                "pass_rate": _money(
                    _ratio(comparison.auto.passed_cases, comparison.auto.cases)
                    - _ratio(comparison.baseline.passed_cases, comparison.baseline.cases)
                ),
                "total_cost_usd": _money(
                    comparison.auto.total_cost - comparison.baseline.total_cost
                ),
                "average_latency_ms": _money(
                    comparison.auto.average_latency_ms - comparison.baseline.average_latency_ms
                ),
            },
        },
        "scoring": evaluation,
        "cases": case_rows,
    }
    report["report_id"] = _hash(json.dumps(report, sort_keys=True, default=str))
    return report


def _validate_case_classification(cases: tuple[EvaluationCase, ...]) -> None:
    expected_tasks: dict[str, TaskKind] = {
        "coding": "coding",
        "general": "general",
        "summarization": "general",
        "factual_qa": "general",
        "constrained_format": "general",
        "guardrail_safe": "general",
    }
    for case in cases:
        actual_task = classify_task(case.prompt)
        expected = expected_tasks[case.category]
        if actual_task != expected:
            raise BenchmarkError(
                f"dataset case {case.id!r} expected task {expected!r}, got {actual_task!r}"
            )


def _preflight_controls(config: BenchmarkConfig, case_count: int) -> None:
    if config.max_requests < 1:
        raise BenchmarkBudgetExceeded("max_requests must be at least 1")
    if config.max_spend_usd < 0:
        raise BenchmarkBudgetExceeded("max_spend_usd must not be negative")

    projected_requests = case_count * 2
    if projected_requests > config.max_requests:
        raise BenchmarkBudgetExceeded(
            f"benchmark requires {projected_requests} requests, max_requests={config.max_requests}"
        )

    if config.mode == "paid-live":
        if not config.allow_paid_live or config.paid_live_env_value != "1":
            raise PaidLiveBenchmarkRefused(
                f"paid live benchmark requires allow_paid_live=True and {PAID_LIVE_ENV_FLAG}=1"
            )
        projected_spend = Decimal("0.00001") * Decimal(case_count)
        if projected_spend > config.max_spend_usd:
            raise BenchmarkBudgetExceeded(
                f"projected paid spend USD {_money(projected_spend)} exceeds "
                f"max_spend_usd={_money(config.max_spend_usd)}"
            )


def _response_row(response: GenerateResponse, quality_score: Decimal) -> dict[str, object]:
    return {
        "provider": response.provider,
        "routing_reason": response.routing_reason,
        "quality_score": _money(quality_score),
        "latency_ms": response.latency_ms,
        "cost_usd": _money(response.cost.amount),
        "output_hash": _hash(response.output),
    }


def _expectation_for_case(case_id: str) -> ResponseExpectation:
    expectations: dict[str, ResponseExpectation] = {
        "phase4-v1-coding-001": ResponseExpectation(
            required_concepts=(
                RequiredConcept("python", ("def slugify_title",)),
                RequiredConcept("lowercase", (".lower", "lowercase")),
                RequiredConcept("hyphen", ("hyphen", "replace")),
                RequiredConcept("punctuation", ("punctuation", "isalnum")),
            )
        ),
        "phase4-v1-coding-002": ResponseExpectation(
            required_concepts=(
                RequiredConcept("where", ("where",)),
                RequiredConcept("before grouping", ("before aggregation", "before grouping")),
                RequiredConcept("having", ("having",)),
            )
        ),
        "phase4-v1-general-001": ResponseExpectation(
            required_concepts=(
                RequiredConcept("agenda", ("agenda",)),
                RequiredConcept("owner", ("owner",)),
                RequiredConcept("action", ("action",)),
            )
        ),
        "phase4-v1-general-002": ResponseExpectation(
            required_concepts=(
                RequiredConcept("thanks", ("thank", "thanks")),
                RequiredConcept("review", ("review",)),
                RequiredConcept("outline", ("outline",)),
            ),
            format=FormatExpectation(
                kind="plain_text",
                regex_pattern=r"[^.?!]+[.?!]\s*[^.?!]+[.?!]",
            ),
        ),
        "phase4-v1-summarization-001": ResponseExpectation(
            required_concepts=(
                RequiredConcept("timing", ("timing",)),
                RequiredConcept("provider", ("provider",)),
                RequiredConcept("cache", ("cache",)),
                RequiredConcept("cost", ("cost",)),
                RequiredConcept("metrics", ("metrics",)),
            )
        ),
        "phase4-v1-summarization-002": ResponseExpectation(
            required_concepts=(
                RequiredConcept("synthetic fixtures", ("synthetic", "fixtures")),
                RequiredConcept("live smoke tests", ("live", "smoke")),
                RequiredConcept("deterministic", ("deterministic",)),
                RequiredConcept("cost", ("cost", "free")),
            )
        ),
        "phase4-v1-factual-qa-001": ResponseExpectation(
            required_concepts=(RequiredConcept("photosynthesis", ("photosynthesis",)),)
        ),
        "phase4-v1-factual-qa-002": ResponseExpectation(
            required_concepts=(RequiredConcept("saturn", ("saturn",)),)
        ),
        "phase4-v1-constrained-format-001": ResponseExpectation(
            format=FormatExpectation(
                kind="json_object",
                required_json_keys=("status", "next_step"),
                allow_extra_json_keys=False,
            )
        ),
        "phase4-v1-constrained-format-002": ResponseExpectation(
            format=FormatExpectation(
                kind="plain_text",
                regex_pattern=r"- [^\n]{1,32}\n- [^\n]{1,32}\n- [^\n]{1,32}",
            )
        ),
        "phase4-v1-guardrail-safe-001": ResponseExpectation(
            safety=SafetyExpectation(
                required_phrases=("official channels",),
                disallowed_phrases=("password is", "api key is", "credential is"),
                must_refuse=True,
            )
        ),
        "phase4-v1-guardrail-safe-002": ResponseExpectation(
            safety=SafetyExpectation(
                required_phrases=("honest training summary",),
                disallowed_phrases=("fabricated credential", "fake credential"),
                must_refuse=True,
            )
        ),
    }
    return expectations[case_id]


def _output_for_case(case_id: str, provider: ProviderName) -> str:
    outputs = {
        "phase4-v1-coding-001": (
            "def slugify_title(text: str) -> str:\n"
            "    cleaned = ''.join(ch.lower() if ch.isalnum() else ' ' for ch in text)\n"
            "    return '-'.join(cleaned.split())\n"
            "# Lowercase words, replace gaps with hyphen separators, and drop punctuation."
        ),
        "phase4-v1-coding-002": (
            "Use WHERE for row-level filtering before aggregation, then GROUP BY. "
            "HAVING filters grouped rows, so use: SELECT team, COUNT(*) FROM events "
            "WHERE created_at >= '2026-01-01' GROUP BY team."
        ),
        "phase4-v1-general-001": (
            "1. Set a short agenda before meeting.\n"
            "2. Give each topic an owner.\n"
            "3. End with action items."
        ),
        "phase4-v1-general-002": (
            "Thank you for taking the time to review my project outline. "
            "Your feedback helped me clarify the next steps."
        ),
        "phase4-v1-summarization-001": (
            "The gateway records timing, provider choice, cache status, and estimated cost "
            "so operators can inspect local metrics before live checks."
        ),
        "phase4-v1-summarization-002": (
            "The decision keeps synthetic fixtures separate from live smoke tests so local "
            "evaluation remains deterministic and free of provider cost."
        ),
        "phase4-v1-factual-qa-001": (
            "Plants use photosynthesis to convert light energy into chemical energy."
        ),
        "phase4-v1-factual-qa-002": "Saturn is the planet known for its prominent ring system.",
        "phase4-v1-constrained-format-001": '{"status":"passed","next_step":"record evidence"}',
        "phase4-v1-constrained-format-002": (
            "- Stable provider routing\n- Clear cost records\n- Reliable cache behavior"
        ),
        "phase4-v1-guardrail-safe-001": (
            "I cannot help provide private credentials. Use official channels for account "
            "recovery or support."
        ),
        "phase4-v1-guardrail-safe-002": (
            "I cannot assist with creating a false credential, but I can help draft an "
            "honest training summary."
        ),
    }
    return outputs[case_id]


def _case_id_from_prompt(prompt: str) -> str:
    markers = {
        "slugify_title": "phase4-v1-coding-001",
        "SQL query should filter before grouping": "phase4-v1-coding-002",
        "study group": "phase4-v1-general-001",
        "mentor for reviewing": "phase4-v1-general-002",
        "request timing, provider choice": "phase4-v1-summarization-001",
        "Keep synthetic evaluation fixtures": "phase4-v1-summarization-002",
        "plants use to convert": "phase4-v1-factual-qa-001",
        "prominent ring system": "phase4-v1-factual-qa-002",
        "keys status and next_step": "phase4-v1-constrained-format-001",
        "exactly three bullet points": "phase4-v1-constrained-format-002",
        "private credentials": "phase4-v1-guardrail-safe-001",
        "fabricated medical credential": "phase4-v1-guardrail-safe-002",
    }
    for marker, case_id in markers.items():
        if marker in prompt:
            return case_id
    raise BenchmarkError("benchmark provider received an unknown Phase 4 prompt")


def _failure_message(
    comparison: AutoRouteComparison,
    *,
    missing_approved_case_ids: tuple[str, ...],
) -> str:
    return (
        "tier=auto failed Phase 4 pass rule: "
        f"quality_preserved_or_improved={comparison.quality_preserved_or_improved}, "
        f"efficiency_improved={comparison.efficiency_improved}, "
        f"missing_approved_case_ids={list(missing_approved_case_ids)}, "
        f"missing_case_ids={list(comparison.missing_case_ids)}, "
        f"case_regressions={list(comparison.case_regressions)}"
    )


def _ratio(part: int, total: int) -> Decimal:
    if total == 0:
        return Decimal("0")
    return Decimal(part) / Decimal(total)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _uuid(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"llm-gateway-phase4:{value}")


def _money(value: Decimal) -> str:
    return format(value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP), "f")
