# Step 4 Dashboard, Alerts, And Runbook

## Edited

- Added Grafana dashboard JSON at `docs/observability/grafana-dashboard.json`.
- Added Prometheus alert rules at `docs/observability/prometheus-alerts.yml`.
- Added operator runbook at `docs/observability/runbook.md`.
- Added validation tests that confirm dashboard and alert queries reference
  metric names defined by `llm_gateway.core.metrics`.
- Added privacy validation tests for the Step 4 observability artifacts.

## Manual Test

1. Run the validation gate:
   `uv run pytest tests/test_observability_docs.py -q`
2. Start the gateway:
   `uv run llm-gateway`
3. Scrape metrics:
   `curl http://127.0.0.1:8000/metrics`
4. Import `docs/observability/grafana-dashboard.json` into Grafana.
5. Load `docs/observability/prometheus-alerts.yml` into Prometheus or a
   compatible rule manager.
6. Confirm dashboard panels populate after traffic reaches `/health/live`,
   `/v1/generate`, and `/metrics`.
7. Confirm alert expressions parse in the target Prometheus environment.
8. Confirm artifact text and query labels do not include sentinel content,
   generated text, bearer-token values, provider-issued request identifiers,
   raw actor values, Redis cache key material, or raw request bodies.
