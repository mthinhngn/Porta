from __future__ import annotations

import json
import re
from pathlib import Path

from llm_gateway.core import metrics

OBSERVABILITY_DIR = Path("docs/observability")
GRAFANA_DASHBOARD = OBSERVABILITY_DIR / "grafana-dashboard.json"
PROMETHEUS_ALERTS = OBSERVABILITY_DIR / "prometheus-alerts.yml"
RUNBOOK = OBSERVABILITY_DIR / "runbook.md"
STEP_NOTE = OBSERVABILITY_DIR / "step-4-dashboard-alerts-runbook.md"

METRIC_PATTERN = re.compile(r"\bllm_gateway_[a-z0-9_]+(?:_bucket|_count|_sum)?\b")
PRIVATE_SENTINELS = (
    "private prompt sentinel",
    "private output sentinel",
    "sk-private-secret-sentinel",
    "Bearer private-token-sentinel",
    "resp_private_provider_request_id",
    "llm-gateway:cache:",
)
FORBIDDEN_LABEL_TOKENS = (
    'prompt="',
    'output="',
    'authorization="',
    'api_key="',
    'actor_id="',
    'provider_request_id="',
    'cache_key="',
    'request_body="',
)


def _defined_metric_names() -> set[str]:
    names: set[str] = set()
    for value in vars(metrics).values():
        metric_name = getattr(value, "_name", None)
        if isinstance(metric_name, str) and metric_name.startswith("llm_gateway_"):
            names.add(metric_name)
            metric_type = getattr(value, "_type", None)
            if metric_type == "counter":
                names.add(f"{metric_name}_total")
    return names


def _base_metric_name(name: str) -> str:
    for suffix in ("_bucket", "_count", "_sum"):
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return name


def _artifact_texts() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in (GRAFANA_DASHBOARD, PROMETHEUS_ALERTS, RUNBOOK, STEP_NOTE)
    }


def test_grafana_dashboard_json_is_valid_and_uses_real_metrics() -> None:
    dashboard = json.loads(GRAFANA_DASHBOARD.read_text(encoding="utf-8"))

    assert dashboard["title"] == "LLM Gateway Observability"
    assert dashboard["panels"]

    defined_metrics = _defined_metric_names()
    referenced_metrics = {
        _base_metric_name(metric_name)
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        for metric_name in METRIC_PATTERN.findall(target.get("expr", ""))
    }

    assert referenced_metrics
    assert referenced_metrics <= defined_metrics


def test_prometheus_alert_rules_reference_real_metrics_and_runbook() -> None:
    alerts_text = PROMETHEUS_ALERTS.read_text(encoding="utf-8")
    defined_metrics = _defined_metric_names()
    referenced_metrics = {
        _base_metric_name(metric_name) for metric_name in METRIC_PATTERN.findall(alerts_text)
    }

    assert "groups:" in alerts_text
    assert "LlmGatewayHigh5xxRate" in alerts_text
    assert "LlmGatewayNoSuccessfulGenerations" in alerts_text
    assert referenced_metrics
    assert referenced_metrics <= defined_metrics
    assert "docs/observability/runbook.md" in alerts_text


def test_runbook_mentions_all_step_four_artifacts_and_metric_families() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "docs/observability/grafana-dashboard.json" in runbook
    assert "docs/observability/prometheus-alerts.yml" in runbook
    for metric_name in sorted(_defined_metric_names()):
        assert metric_name in runbook


def test_observability_artifacts_do_not_include_private_or_high_cardinality_labels() -> None:
    for path, text in _artifact_texts().items():
        for sentinel in PRIVATE_SENTINELS:
            assert sentinel not in text, path
        for token in FORBIDDEN_LABEL_TOKENS:
            assert token not in text, path
