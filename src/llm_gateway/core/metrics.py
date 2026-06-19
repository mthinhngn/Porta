"""Prometheus metrics with privacy-safe bounded labels."""

from __future__ import annotations

from collections.abc import Collection

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client import generate_latest as prometheus_generate_latest

from llm_gateway.core.logging import REDACTED, sanitize_log_message

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
UNKNOWN_LABEL = "unknown"
ALLOWED_HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
ALLOWED_PROVIDERS = frozenset({"openai", "llama", "qwen"})
ALLOWED_CACHE_STATUSES = frozenset(
    {
        "disabled",
        "hit",
        "miss",
        "publish_failure",
        "publish_success",
        "reservation",
        "unavailable",
    }
)
ALLOWED_GENERATE_STAGES = frozenset(
    {"auth", "guardrail", "quota", "cache", "provider", "ledger", "generate"}
)
ALLOWED_GENERATE_RESULTS = frozenset(
    {
        "allow",
        "block",
        "disabled",
        "failure",
        "hit",
        "miss",
        "reservation",
        "success",
        "unavailable",
    }
)
ALLOWED_ATTEMPT_STATUSES = frozenset({"failed", "succeeded", "timed_out"})

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS_TOTAL = Counter(
    "llm_gateway_http_requests_total",
    "HTTP requests completed by the gateway.",
    ("method", "route", "status_code", "status_family"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "llm_gateway_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route", "status_code", "status_family"),
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
AUTH_EVENTS_TOTAL = Counter(
    "llm_gateway_auth_events_total",
    "Gateway authentication events.",
    ("result", "error_code"),
    registry=REGISTRY,
)
GUARDRAIL_EVENTS_TOTAL = Counter(
    "llm_gateway_guardrail_events_total",
    "Gateway guardrail decisions.",
    ("result", "error_code"),
    registry=REGISTRY,
)
QUOTA_EVENTS_TOTAL = Counter(
    "llm_gateway_quota_events_total",
    "Gateway quota decisions.",
    ("result", "error_code"),
    registry=REGISTRY,
)
CACHE_EVENTS_TOTAL = Counter(
    "llm_gateway_cache_events_total",
    "Gateway response cache events.",
    ("result", "model_alias", "cache_status", "error_code"),
    registry=REGISTRY,
)
GENERATE_EVENTS_TOTAL = Counter(
    "llm_gateway_generate_events_total",
    "Generate request pipeline events.",
    ("stage", "result", "provider", "model_alias", "cache_status", "error_code"),
    registry=REGISTRY,
)
GENERATE_DURATION_SECONDS = Histogram(
    "llm_gateway_generate_duration_seconds",
    "Generate request duration in seconds.",
    ("result", "provider", "model_alias", "cache_status", "error_code"),
    registry=REGISTRY,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
PROVIDER_ATTEMPTS_TOTAL = Counter(
    "llm_gateway_provider_attempts_total",
    "Provider attempts made by the gateway.",
    ("provider", "model_alias", "attempt_status", "error_code"),
    registry=REGISTRY,
)
PROVIDER_ATTEMPT_DURATION_SECONDS = Histogram(
    "llm_gateway_provider_attempt_duration_seconds",
    "Provider attempt duration in seconds.",
    ("provider", "model_alias", "attempt_status", "error_code"),
    registry=REGISTRY,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
LEDGER_OPERATION_DURATION_SECONDS = Histogram(
    "llm_gateway_ledger_operation_duration_seconds",
    "Synchronous ledger operation duration in seconds.",
    ("operation", "result"),
    registry=REGISTRY,
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


def sanitize_bounded_label(
    value: object,
    *,
    allowed: Collection[str] | None = None,
    max_length: int = 128,
    default: str = UNKNOWN_LABEL,
) -> str:
    """Return a bounded operational metric label, never raw suspicious content."""

    if not isinstance(value, str):
        return default
    normalized = value.strip()
    if not normalized:
        return default
    if allowed is not None:
        return normalized if normalized in allowed else default
    if len(normalized) > max_length or "\r" in normalized or "\n" in normalized:
        return default
    if sanitize_log_message(normalized) == REDACTED:
        return default
    return normalized


def http_method_label(method: object) -> str:
    if not isinstance(method, str):
        return UNKNOWN_LABEL
    return sanitize_bounded_label(method.upper(), allowed=ALLOWED_HTTP_METHODS)


def route_label(route_template: str | None) -> str:
    if route_template is None:
        return UNKNOWN_LABEL
    if not route_template.startswith("/") or "?" in route_template:
        return UNKNOWN_LABEL
    return sanitize_bounded_label(route_template)


def provider_label(provider: object) -> str:
    return sanitize_bounded_label(provider, allowed=ALLOWED_PROVIDERS)


def model_alias_label(model_alias: object) -> str:
    return sanitize_bounded_label(model_alias, max_length=64)


def cache_status_label(cache_status: object) -> str:
    return sanitize_bounded_label(cache_status, allowed=ALLOWED_CACHE_STATUSES)


def error_code_label(error_code: object) -> str:
    if error_code is None:
        return "none"
    return sanitize_bounded_label(error_code, max_length=64)


def status_labels(status_code: int) -> tuple[str, str]:
    if status_code < 100 or status_code > 599:
        return UNKNOWN_LABEL, UNKNOWN_LABEL
    return str(status_code), f"{status_code // 100}xx"


def record_http_request(
    *,
    method: object,
    route_template: str | None,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record HTTP request metrics using only trusted low-cardinality labels."""

    code, family = status_labels(status_code)
    labels = {
        "method": http_method_label(method),
        "route": route_label(route_template),
        "status_code": code,
        "status_family": family,
    }
    HTTP_REQUESTS_TOTAL.labels(**labels).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(max(duration_seconds, 0.0))


def record_auth_event(*, result: str, error_code: object = None) -> None:
    AUTH_EVENTS_TOTAL.labels(
        result=sanitize_bounded_label(result, allowed={"success", "failure", "unavailable"}),
        error_code=error_code_label(error_code),
    ).inc()


def record_guardrail_event(*, result: str, error_code: object = None) -> None:
    bounded_result = sanitize_bounded_label(result, allowed={"allow", "block"})
    GUARDRAIL_EVENTS_TOTAL.labels(
        result=bounded_result,
        error_code=error_code_label(error_code),
    ).inc()
    record_generate_event(
        stage="guardrail",
        result=bounded_result,
        error_code=error_code,
    )


def record_quota_event(*, result: str, error_code: object = None) -> None:
    bounded_result = sanitize_bounded_label(
        result,
        allowed={"allow", "disabled", "exceeded", "unavailable"},
    )
    QUOTA_EVENTS_TOTAL.labels(
        result=bounded_result,
        error_code=error_code_label(error_code),
    ).inc()
    generate_result = "failure" if bounded_result == "exceeded" else bounded_result
    record_generate_event(
        stage="quota",
        result=generate_result,
        error_code=error_code,
    )


def record_cache_event(
    *,
    result: str,
    model_alias: object,
    cache_status: object,
    error_code: object = None,
) -> None:
    bounded_result = sanitize_bounded_label(
        result,
        allowed={"disabled", "hit", "miss", "reservation", "success", "unavailable"},
    )
    bounded_cache_status = cache_status_label(cache_status)
    bounded_model = model_alias_label(model_alias)
    CACHE_EVENTS_TOTAL.labels(
        result=bounded_result,
        model_alias=bounded_model,
        cache_status=bounded_cache_status,
        error_code=error_code_label(error_code),
    ).inc()
    record_generate_event(
        stage="cache",
        result=bounded_result,
        model_alias=bounded_model,
        cache_status=bounded_cache_status,
        error_code=error_code,
    )


def record_generate_event(
    *,
    stage: str,
    result: str,
    provider: object = None,
    model_alias: object = None,
    cache_status: object = "disabled",
    error_code: object = None,
) -> None:
    GENERATE_EVENTS_TOTAL.labels(
        stage=sanitize_bounded_label(stage, allowed=ALLOWED_GENERATE_STAGES),
        result=sanitize_bounded_label(result, allowed=ALLOWED_GENERATE_RESULTS),
        provider=provider_label(provider),
        model_alias=model_alias_label(model_alias),
        cache_status=cache_status_label(cache_status),
        error_code=error_code_label(error_code),
    ).inc()


def record_generate_duration(
    *,
    result: str,
    provider: object = None,
    model_alias: object = None,
    cache_status: object = "disabled",
    error_code: object = None,
    duration_seconds: float,
) -> None:
    GENERATE_DURATION_SECONDS.labels(
        result=sanitize_bounded_label(result, allowed={"failure", "success"}),
        provider=provider_label(provider),
        model_alias=model_alias_label(model_alias),
        cache_status=cache_status_label(cache_status),
        error_code=error_code_label(error_code),
    ).observe(max(duration_seconds, 0.0))


def record_provider_attempt(
    *,
    provider: object,
    model_alias: object,
    attempt_status: str,
    error_code: object = None,
    duration_seconds: float,
) -> None:
    labels = {
        "provider": provider_label(provider),
        "model_alias": model_alias_label(model_alias),
        "attempt_status": sanitize_bounded_label(
            attempt_status,
            allowed=ALLOWED_ATTEMPT_STATUSES,
        ),
        "error_code": error_code_label(error_code),
    }
    PROVIDER_ATTEMPTS_TOTAL.labels(**labels).inc()
    PROVIDER_ATTEMPT_DURATION_SECONDS.labels(**labels).observe(max(duration_seconds, 0.0))


def record_ledger_operation(
    *,
    operation: object,
    result: str,
    duration_seconds: float,
) -> None:
    LEDGER_OPERATION_DURATION_SECONDS.labels(
        operation=sanitize_bounded_label(operation, max_length=64),
        result=sanitize_bounded_label(result, allowed={"failure", "success"}),
    ).observe(max(duration_seconds, 0.0))


def generate_metrics() -> bytes:
    return prometheus_generate_latest(REGISTRY)
