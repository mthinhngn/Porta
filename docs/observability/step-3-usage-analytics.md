# Step 3 Usage Analytics

## Edited

- Added admin-only gateway API key support through `GatewayApiKeyConfig.is_admin`
  and `AuthenticatedActor.is_admin`.
- Protected `/v1/analytics/*` with the existing gateway bearer-token
  middleware.
- Added `GET /v1/analytics/usage/summary`, which returns aggregate ledger
  analytics only:
  - request counts by status
  - provider/model attempt counts
  - usage row and token totals
  - cost totals by currency
  - reconciliation anomaly counts
- Added analytics response contracts under `llm_gateway.domain.analytics`.
- Stored the configured SQLAlchemy session factory on app state so analytics
  can query the same ledger database as generation.
- Added tests for admin access, non-admin denial, empty ledgers, aggregate
  correctness, filtering, reconciliation anomalies, and privacy-safe responses.

## Manual Test

1. Configure at least one admin gateway key:
   `{"key": "admin-key", "actor_id": "...", "api_key_id": "...", "is_admin": true}`.
2. Start the app with a configured `LLM_GATEWAY_DATABASE_URL`.
3. Generate one or more requests through `POST /v1/generate`.
4. Call:
   `GET /v1/analytics/usage/summary`
   with `Authorization: Bearer admin-key`.
5. Optional filters:
   - `provider=openai`
   - `model=gateway-default`
   - `status=succeeded`
   - `from_time=<ISO datetime>`
   - `to_time=<ISO datetime>`
6. Confirm the response contains only aggregate sections:
   `request_statuses`, `provider_model_attempts`, `usage`, `costs`, and
   `reconciliation`.
7. Confirm a non-admin key returns `403 analytics_access_denied`.
8. Confirm the response does not contain prompts, outputs, API keys, bearer
   tokens, raw actor IDs, provider request IDs, correlation IDs, or raw rows.
