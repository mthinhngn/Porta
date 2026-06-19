# Step 2 Generate Pipeline Metrics

## Edited

- Extended `llm_gateway.core.metrics` with counters and histograms for auth,
  guardrails, quota, cache, generate outcomes, provider attempts, and ledger
  operation latency.
- Instrumented `GatewayAuthMiddleware`, the `/v1/generate` route, and
  `GenerationService` without changing the runtime order.
- Added focused metrics tests for auth failures, guardrail blocks, quota
  outcomes, cache hits/misses, provider success/failure, retry/fallback, ledger
  latency, and privacy-safe metric output.

## Manual Test

1. Start the app with a configured test key and generation service.
2. Call `GET /metrics` and confirm Prometheus text is returned.
3. Send `POST /v1/generate` with a valid key and a safe prompt.
4. Send `POST /v1/generate` without a key and confirm auth failure metrics.
5. Send a blocked prompt containing the configured guardrail test token.
6. Call `GET /metrics` again and check these metric families:
   - `llm_gateway_auth_events_total`
   - `llm_gateway_guardrail_events_total`
   - `llm_gateway_quota_events_total`
   - `llm_gateway_cache_events_total`
   - `llm_gateway_generate_events_total`
   - `llm_gateway_generate_duration_seconds`
   - `llm_gateway_provider_attempts_total`
   - `llm_gateway_provider_attempt_duration_seconds`
   - `llm_gateway_ledger_operation_duration_seconds`
7. Confirm the metrics output does not contain prompt text, generated output,
   API keys, bearer tokens, raw actor IDs, provider request IDs, or cache keys.
