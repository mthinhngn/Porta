# LLM Gateway Observability Runbook

## Start And Scrape

1. Install and sync dependencies:
   `uv sync --frozen`
2. Start the gateway:
   `uv run llm-gateway`
3. Check liveness:
   `curl http://127.0.0.1:8000/health/live`
4. Scrape metrics:
   `curl http://127.0.0.1:8000/metrics`
5. Import `docs/observability/grafana-dashboard.json` into Grafana and select
   the Prometheus data source.
6. Load `docs/observability/prometheus-alerts.yml` into Prometheus or the
   compatible rule manager used by the deployment.

## Expected Metric Families

- `llm_gateway_http_requests_total`
- `llm_gateway_http_request_duration_seconds`
- `llm_gateway_auth_events_total`
- `llm_gateway_guardrail_events_total`
- `llm_gateway_quota_events_total`
- `llm_gateway_cache_events_total`
- `llm_gateway_generate_events_total`
- `llm_gateway_generate_duration_seconds`
- `llm_gateway_provider_attempts_total`
- `llm_gateway_provider_attempt_duration_seconds`
- `llm_gateway_ledger_operation_duration_seconds`

## Privacy Validation

1. Send a generate request with a unique sentinel phrase.
2. Scrape `/metrics`.
3. Call `/v1/analytics/usage/summary` with an admin key.
4. Search the metrics output, analytics response, dashboard JSON, alert rules,
   and this runbook for the sentinel phrase.
5. The sentinel must not appear. The same check applies to generated text,
   bearer-token values, provider-issued request identifiers, raw actor values,
   and Redis cache key material.

## High 5xx Rate

Alert: `LlmGatewayHigh5xxRate`

Check recent request logs by route template and status family. Then inspect
provider, quota, cache, and ledger panels to locate the failing stage.

## Provider Failures

Alert: `LlmGatewayProviderFailuresSpike`

Check `llm_gateway_provider_attempts_total` by provider and error code. If only
one provider is affected, validate that fallback is still succeeding. If all
providers are affected, inspect gateway configuration and network reachability.

## Quota Unavailable

Alert: `LlmGatewayQuotaUnavailable`

Validate Redis health and gateway Redis configuration. This failure blocks
quota-protected generate requests before cache or provider execution.

## Cache Coordination

Alert: `LlmGatewayCacheCoordinationLoss`

Inspect Redis availability and cache lease behavior. Cache publication failures
should not expose content, but repeated coordination loss can reduce cache hit
rate and increase provider traffic.

## Ledger And Reconciliation

Alert: `LlmGatewayLedgerOperationFailures`

Inspect database connectivity and migration state. Then call the admin analytics
summary endpoint and review the aggregate reconciliation counts for succeeded
requests without usage and usage rows without succeeded attempts.

## Latency

Alerts: `LlmGatewayHighGenerateLatencyP95`, `LlmGatewayHighProviderLatencyP95`

Compare generate p95 against provider attempt p95. If provider latency is low
but generate latency is high, inspect quota, cache, and ledger panels.

## No Successes

Alert: `LlmGatewayNoSuccessfulGenerations`

This means generate traffic is arriving but no successful generate event was
recorded. Check auth, guardrail, quota, cache, provider, and ledger panels in
that order.
