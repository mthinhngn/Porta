# LLM Gateway

LLM Gateway is a privacy-conscious, operator-focused gateway for routing text
generation requests across LLM providers while keeping authentication, quotas,
cache control, cost accounting, observability, and release gates in one place.

The platform includes the main FastAPI gateway, a non-streaming
`POST /v1/generate` API, OpenAI Responses integration, local Ollama fallbacks,
Redis-backed quota and response cache controls, a SQL usage ledger, Prometheus
metrics, admin analytics, a React dashboard, a lightweight local test console,
evaluation-driven routing, production deployment assets, CI/CD security gates,
and final launch evidence.

## Product Capabilities

| Area | What it provides |
| --- | --- |
| Gateway API | FastAPI app with health probes, `POST /v1/generate`, and admin analytics. |
| Provider routing | OpenAI primary path, controlled retry, local fallback to Qwen or Llama, and policy-based routing for cost, latency, and quality. |
| Auth and policy | Gateway bearer-token auth, per-key actor mapping, admin controls, provider allowlists, and request quota policy. |
| Guardrails | Local pre-provider guardrail checks with sanitized block responses. |
| Quota and cache | Redis quota enforcement and encrypted actor-scoped response cache. |
| Ledger and pricing | SQLAlchemy ledger for requests, attempts, pricing snapshots, token usage, cost, and reconciliation invariants. |
| Observability | Prometheus `/metrics`, Grafana dashboard JSON, alert rules, runbooks, and operator evidence. |
| Usage analytics | Admin-only aggregate endpoint for request, attempt, usage, cost, and reconciliation summaries. |
| React dashboard | Vite/React dashboard for gateway pulse, live generation, cost guardrails, and operator monitoring. |
| Local test console | `app.py` browser console for health, metrics, generation, charts, and admin analytics checks. |
| Production deployment | Containers, load testing, Kubernetes/Helm deployment, CI/CD, security automation, and release gate evidence. |

## Architecture

LLM Gateway is the control plane between applications and model providers:

```mermaid
flowchart LR
    Apps["Apps and internal tools"] --> Gateway["LLM Gateway API"]

    Gateway --> Auth["API key auth<br/>actor and policy lookup"]
    Auth --> Guardrails["Guardrails<br/>local allow/block checks"]
    Guardrails --> Quota["Quota<br/>Redis per-actor limits"]
    Quota --> Cache["Encrypted cache<br/>actor + request + policy scope"]
    Cache --> Router["Routing engine<br/>task, cost, latency, quality policy"]

    Router --> OpenAI["OpenAI Responses"]
    Router --> Local["Local Ollama<br/>Qwen / Llama"]
    Router --> MoreProviders["Additional provider adapters"]

    OpenAI --> Ledger["SQL ledger<br/>requests, attempts, usage, pricing"]
    Local --> Ledger
    MoreProviders --> Ledger

    Ledger --> Analytics["Admin analytics"]
    Gateway --> Metrics["Prometheus metrics"]
    Metrics --> Grafana["Grafana dashboard and alerts"]
    Analytics --> Operators["Operator dashboards and release evidence"]
```

Clients call one stable gateway API while the gateway handles provider choice,
retries, fallback, policy enforcement, cost tracking, privacy boundaries, and
operational visibility.

## Request Flow

The runtime request path is intentionally ordered so failures stop as early as
possible:

```mermaid
flowchart TD
    Client["Client / UI"] --> API["POST /v1/generate"]

    API --> Auth["1. Authenticate gateway key"]
    Auth -->|fail| AuthFail["401 or 403<br/>sanitized error"]

    Auth -->|pass| Guardrail["2. Guardrail decision"]
    Guardrail -->|block| Blocked["400 sanitized denial<br/>no provider call"]

    Guardrail -->|allow| Quota["3. Redis quota"]
    Quota -->|exceeded| QuotaFail["429 quota exceeded"]

    Quota -->|ok| Cache["4. Encrypted response cache"]
    Cache -->|hit| CacheHit["Return cached GenerateResponse"]
    Cache -->|miss| Classifier["5. Routing policy"]

    Classifier -->|coding| Coding["OpenAI -> OpenAI retry -> Qwen -> Llama"]
    Classifier -->|general| General["OpenAI -> OpenAI retry -> Llama -> Qwen"]

    Coding --> Provider["6. Provider execution<br/>shared deadline"]
    General --> Provider

    Provider -->|success| Ledger["7. SQL ledger<br/>one winning usage row"]
    Provider -->|failure| Failure["7. SQL ledger<br/>failed request and attempts"]

    Ledger --> CacheWrite["8. Cache successful response"]
    CacheWrite --> Response["GenerateResponse"]
    Failure --> Error["Sanitized provider error"]
```

Short-circuit behavior:

- Authentication failures stop before guardrails, quota, cache, providers, and
  persistence.
- Guardrail blocks stop before quota, cache, providers, usage rows, and charges.
- Quota failures stop before cache and provider execution.
- Cache hits return without a provider call or new usage row.
- Provider success creates one winning usage row, calculates cost, and can cache
  the normalized response.
- Provider failure records the failed request and attempts, but writes no usage
  row and no cache entry.

## API Surface

### Health

- `GET /health/live`
- `GET /health/ready`

Readiness checks configuration, Redis, and required local Ollama models when
local providers are enabled.

### Metrics

- `GET /metrics`

Exports Prometheus metrics for HTTP traffic, auth, guardrails, quota, cache,
generation events, provider attempts, provider latency, generation latency, and
ledger operations.

### Generate

- `POST /v1/generate`
- Requires `Authorization: Bearer <gateway_api_key>`

Request:

```json
{
  "model": "gateway-default",
  "input": "Respond with exactly: hello",
  "tier": "standard",
  "temperature": 0.7,
  "top_p": 1,
  "max_output_tokens": 64
}
```

`tier` is optional. `standard` preserves the deterministic OpenAI-first route.
`auto` uses the Phase 4 evidence-gated policy, which prefers the approved local
provider for the detected task category only when the benchmark evidence is
accepted.

Response:

```json
{
  "request_id": "00000000-0000-0000-0000-000000000000",
  "output": "hello",
  "provider": "openai",
  "model": "gateway-default",
  "tokens": {
    "input_tokens": 10,
    "output_tokens": 2,
    "total_tokens": 12
  },
  "cost": {
    "amount": "0.0000042000",
    "currency": "USD"
  },
  "routing_reason": "configured_single_path",
  "cache_status": "miss",
  "served_from_cache": false,
  "attempt_count": 1,
  "latency_ms": 420
}
```

### Analytics

- `GET /v1/analytics/usage/summary`
- Requires an admin gateway key.
- Supports optional filters for provider, model, status, and time window.
- Returns aggregate request statuses, provider/model attempt counts, token
  totals, cost totals, and reconciliation anomaly counts.

Analytics responses are designed to avoid prompts, outputs, bearer tokens,
raw actor IDs, provider request IDs, and raw ledger rows.

## Repository Map

```text
src/llm_gateway/api/          FastAPI route composition
src/llm_gateway/core/         auth, config, errors, guardrails, quota, cache, metrics
src/llm_gateway/domain/       public request/response and analytics contracts
src/llm_gateway/providers/    OpenAI and Ollama provider adapters
src/llm_gateway/persistence/  SQLAlchemy models, metadata, and ledger transactions
src/llm_gateway/services/     generation orchestration and provider fallback
src/components/dashboard/     React dashboard panels
src/api/gateway.ts            typed browser client for POST /v1/generate
docs/                         architecture, privacy, UI, and observability docs
alembic/                      database migrations
tests/                        unit, contract, integration, metrics, and smoke tests
app.py                        local browser test console
```

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm for the React dashboard
- Redis for readiness, quotas, and cache
- PostgreSQL-compatible database for persistence
- Ollama for local fallback testing
- An OpenAI API key for live OpenAI generation

Local fallback models:

```powershell
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:3b
```

## Setup

Install Python dependencies:

```powershell
uv python install 3.12
uv sync --frozen
```

Install frontend dependencies:

```powershell
npm install
```

Create local environment files:

```powershell
Copy-Item .env.example .env
New-Item -ItemType File -Force .env.local
```

Use `.env` for shared local defaults and `.env.local` for private machine-only
secrets. The gateway loads `.env` first and `.env.local` second, so `.env.local`
can override local defaults. Do not put real secrets in `.env.example`.

Important backend settings:

- `LLM_GATEWAY_DATABASE_URL`
- `LLM_GATEWAY_REDIS_URL`
- `LLM_GATEWAY_OPENAI_API_KEY`
- `LLM_GATEWAY_GATEWAY_API_KEYS`
- `LLM_GATEWAY_GATEWAY_CACHE_ENCRYPTION_KEY`
- `LLM_GATEWAY_OLLAMA_BASE_URL`

Example local secrets:

```powershell
Add-Content .env.local 'LLM_GATEWAY_OPENAI_API_KEY=your-real-openai-key'
Add-Content .env.local 'LLM_GATEWAY_GATEWAY_CACHE_ENCRYPTION_KEY=replace-with-32-byte-url-safe-base64'
```

Example gateway keys setting:

```powershell
Add-Content .env.local 'LLM_GATEWAY_GATEWAY_API_KEYS=[{"api_key_id":"00000000-0000-0000-0000-000000000001","actor_id":"00000000-0000-0000-0000-000000000101","key":"user-test-key","enabled":true,"request_quota_limit":60},{"api_key_id":"00000000-0000-0000-0000-000000000002","actor_id":"00000000-0000-0000-0000-000000000102","key":"admin-test-key","enabled":true,"is_admin":true,"request_quota_limit":120}]'
```

Generate a cache key locally:

```powershell
uv run python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

## Database

Run migrations before using the generation ledger:

```powershell
uv run alembic upgrade head
```

Useful migration checks:

```powershell
uv run python -m alembic heads
uv run python -m alembic upgrade head --sql
uv run alembic revision --autogenerate -m "describe change"
```

Runtime database URLs should use the synchronous psycopg driver:

```text
postgresql+psycopg://user:password@host/database
```

Bare `postgresql://` URLs are normalized to psycopg. `postgresql+asyncpg://`
is reserved for Alembic migrations and is rejected for the runtime ledger.

## Run The Backend

Packaged entrypoint:

```powershell
uv run llm-gateway
```

Local reload:

```powershell
uv run uvicorn llm_gateway.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

The packaged server disables Uvicorn raw access logs so prompts, query strings,
and credentials are not accidentally printed through access-log lines.

## Run The React Dashboard

The Vite dashboard calls the backend through the dev proxy. Add frontend values
to `.env.local`:

```powershell
Add-Content .env.local 'VITE_LLM_GATEWAY_BACKEND_URL=http://127.0.0.1:8000'
Add-Content .env.local 'VITE_LLM_GATEWAY_MODEL=gateway-default'
Add-Content .env.local 'VITE_LLM_GATEWAY_API_KEY=user-test-key'
```

`VITE_*` values are visible to browser code, so use only local or development
gateway keys.

Start the backend in one terminal:

```powershell
uv run uvicorn llm_gateway.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

Start the React app in another terminal:

```powershell
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

Dashboard routes:

- `/#gateway` for the gateway pulse panel.
- `/#gateway-light` for the light variant.
- `/#playground` for the live generation playground.
- `/#cost` for the cost guard panel.

## Run The Local Test Console

`app.py` is a small browser UI for local manual checks. It can call health,
metrics, generation, and admin analytics, and it renders simple charts from the
Prometheus scrape.

Start the backend:

```powershell
uv run llm-gateway
```

Start the console:

```powershell
uv run python app.py
```

Open:

```text
http://127.0.0.1:8501
```

Suggested flow:

1. Use gateway URL `http://127.0.0.1:8000`.
2. Use `user-test-key` for generation.
3. Use `admin-test-key` for analytics.
4. Check health.
5. Check metrics.
6. Run a prompt.
7. Refresh metrics.
8. Load analytics.

## Quality Checks

Core checks:

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run python -m alembic heads
uv run python -m alembic upgrade head --sql
```

Frontend checks:

```powershell
npm run build
```

Optional local integration checks:

```powershell
$env:LLM_GATEWAY_LOCAL_SMOKE="1"
uv run pytest tests/test_local_smoke.py -q

$env:LLM_GATEWAY_REAL_REDIS_TEST="1"
uv run pytest tests/test_real_redis.py -q
```

Optional live OpenAI smoke:

```powershell
$env:LLM_GATEWAY_LIVE_SMOKE="1"
uv run pytest tests/test_live_smoke.py -q
```

The live smoke can incur provider cost and requires a real
`LLM_GATEWAY_OPENAI_API_KEY`.

Phase 4 local benchmark:

```powershell
uv run python scripts/run_phase4_benchmark.py --mode local --report-path reports/phase4-benchmark.json
```

The local benchmark is deterministic, uses synthetic fixtures, and does not call
paid providers. Paid live benchmark mode is refused unless both
`--allow-paid-live` and `LLM_GATEWAY_PHASE4_PAID_LIVE=1` are set, and request
and spend caps still apply.

## Privacy Model

The gateway is built around content minimization:

- Prompts and generated outputs are not logged or persisted by default.
- OpenAI requests use `store=false`.
- Provider errors are normalized before reaching clients or persistence.
- Redis cache keys use scoped fingerprints, not raw prompt text.
- Cache values are encrypted when response caching is enabled.
- API keys and provider secrets stay in environment configuration.
- Metrics use bounded operational labels and do not include content.
- Admin analytics returns aggregate summaries rather than raw prompt records.
- Uvicorn raw access logs are disabled in the packaged server.

See [docs/privacy.md](docs/privacy.md) for the detailed handling policy.

## Observability

Operator artifacts:

- `GET /metrics`
- `docs/observability/grafana-dashboard.json`
- `docs/observability/prometheus-alerts.yml`
- `docs/observability/runbook.md`
- `docs/observability/step-3-usage-analytics.md`
- `docs/observability/step-4-dashboard-alerts-runbook.md`

Expected metric families include:

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

The operational runbook covers high 5xx rates, provider failures, quota
unavailability, cache coordination loss, ledger failures, latency, and missing
successful generations.

## Delivery Lifecycle

The project is organized as a gated delivery lifecycle from foundation through
release:

| Phase | Capability | Review focus |
| --- | --- | --- |
| 0. Foundation | Package structure, settings, health routes, sanitized errors, correlation IDs, and persistence baseline. | App boots cleanly, health probes work, privacy/logging boundaries hold. |
| 1. Generate Slice | `POST /v1/generate`, OpenAI Responses adapter, usage normalization, Decimal pricing, request/attempt/usage ledger. | One successful request creates exactly one chargeable usage row and provider errors stay sanitized. |
| 2. Gateway Controls | API-key auth, actor policy, guardrails, quota, encrypted cache, local fallback, and deterministic baseline routing. | Runtime order stays auth -> guardrail -> quota -> cache -> provider -> ledger; failed attempts are recorded without duplicate charges. |
| 3. Observability | Prometheus metrics, admin analytics, Grafana dashboard, alerts, runbooks, and operator console. | Metrics and analytics expose operational facts without content, secrets, raw actors, or provider IDs. |
| 4. Evaluation and Auto-Routing | Evaluation datasets, model scorecards, cost/latency/quality policies, and adaptive routing decisions. | Routing improves quality or cost without breaking policy, privacy, or ledger invariants. |
| 5. Containers and Load | Container images, production config, load tests, pool tuning, failure drills, and backup/restore checks. | Gateway handles realistic traffic with stable latency, clean degradation, and reproducible deployment. |
| 6. Kubernetes and Helm | Helm chart, manifests, secrets integration, scaling, probes, and rollout strategy. | Deployment is repeatable, configurable, and safe to roll forward or roll back. |
| 7. CI/CD and Security | GitHub Actions, lint/type/test gates, migration checks, secret scanning, dependency review, and image scanning. | No release can bypass quality, migration, privacy, or security checks. |
| 8. Release Evidence | Launch gate, operator docs, evidence bundle, reviewed commit SHA, and production readiness checklist. | A separate read-only review confirms the exact release commit is ready. |

Project governance rule: each phase is reviewed against the exact commit SHA
that advances, and the next phase starts only after the read-only review records
`CORE REVIEW: PASS`.

## Useful Docs

- [docs/architecture.md](docs/architecture.md)
- [docs/privacy.md](docs/privacy.md)
- [docs/app-ui-guide.md](docs/app-ui-guide.md)
- [docs/observability/runbook.md](docs/observability/runbook.md)
