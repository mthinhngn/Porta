# LLM Gateway

A privacy-conscious Python LLM gateway for authenticated text generation,
provider routing, usage accounting, quotas, caching, and local fallback models.

```mermaid
flowchart TD
    Client["Client / App"] --> API["POST /v1/generate"]

    API --> Auth["1. Auth<br/>Validate gateway API key"]
    Auth -->|fail| AuthFail["401 sanitized error<br/>No DB row, no provider call"]

    Auth -->|pass| Guardrail["2. Guardrail<br/>Input allow/block"]
    Guardrail -->|block| Blocked["400 sanitized denial<br/>No quota, cache, provider, usage, or charge"]

    Guardrail -->|allow| Quota["3. Quota<br/>Atomic Redis per-actor limit"]
    Quota -->|exceeded| QuotaFail["429 quota exceeded<br/>No cache, provider, usage, or charge"]

    Quota -->|ok| Cache["4. Cache<br/>Actor + request + guardrail version"]
    Cache -->|hit| CacheHit["Return cached response<br/>No provider call<br/>No new usage row"]

    Cache -->|miss| Classifier["5. Task Classifier<br/>Deterministic local rules"]

    Classifier -->|coding prompt| CodingRoute["Coding Route<br/>OpenAI -> OpenAI retry -> Qwen -> Llama"]
    Classifier -->|general prompt| GeneralRoute["General Route<br/>OpenAI -> OpenAI retry -> Llama -> Qwen"]

    CodingRoute --> ProviderExec["6. Provider Execution<br/>One shared deadline<br/>Never retry local models"]
    GeneralRoute --> ProviderExec

    ProviderExec -->|success| Ledger["7. SQL Ledger<br/>Provider attempts + one winning usage row"]
    ProviderExec -->|total failure| FailureLedger["7. SQL Ledger<br/>Failed request + failed attempts<br/>No usage row"]
    FailureLedger --> ProviderFail["Sanitized provider failure<br/>No cache write<br/>No usage row"]

    Ledger --> CacheWrite["8. Cache Write<br/>Only successful normalized responses"]
    CacheWrite --> Response["GenerateResponse<br/>provider, output, tokens, cost"]

    Quota -.uses.-> Redis[("Redis<br/>quota + cache")]
    Cache -.uses.-> Redis
    Ledger -.writes.-> DB[("Database<br/>requests, attempts, usage, pricing")]
    FailureLedger -.writes.-> DB
```

## What It Does

- Exposes `POST /v1/generate` through FastAPI.
- Authenticates callers with gateway API keys.
- Resolves each key to one internal actor identity.
- Runs guardrails before quota, cache, or provider execution.
- Enforces per-actor Redis quotas.
- Serves actor-scoped encrypted Redis cache hits without new provider calls or
  charges.
- Uses a single-flight cache reservation so identical concurrent misses do not
  all call the provider.
- Uses OpenAI as the primary provider.
- Falls back to local Ollama models when OpenAI has retryable failures.
- Persists sanitized request, provider-attempt, pricing, and usage records.
- Calculates cost with `Decimal` and keeps local fallback pricing at zero USD.

## Request Flow

```text
auth -> guardrail -> quota -> cache -> provider retry/fallback -> persist
```

Short-circuit behavior:

- Authentication failure stops before guardrails, quota, cache, providers, and
  persistence.
- Guardrail block returns a sanitized denial with no quota use, cache access,
  provider call, usage row, or charge.
- Quota failure stops before cache and provider execution.
- Cache hit returns the cached response with no provider call and no new usage
  row.
- Provider success creates exactly one winning usage row and then caches the
  normalized response.
- Provider failure persists the failed request and attempts, then writes no
  cache entry and no usage row.

## Routing Summary

Guardrails do not call OpenAI, Qwen, or Llama. They are deterministic local
checks that return only `allow` or `block`.

After guardrails allow a request, the gateway classifies the prompt with local
deterministic rules:

- Coding indicators include code fences, stack traces, file extensions, SQL,
  `code`, `function`, `class`, `debug`, `implement`, `refactor`, and `regex`.
- Prompts without coding indicators are treated as general prompts.

Fallback order:

- Coding prompt: `OpenAI -> OpenAI retry -> Qwen -> Llama`
- General prompt: `OpenAI -> OpenAI retry -> Llama -> Qwen`

Only OpenAI receives the same-provider retry. Each local Ollama fallback model
gets at most one attempt, and all attempts share one deadline.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Redis for readiness, quotas, and cache
- Ollama for local fallback smokes
- PostgreSQL-compatible database for production persistence

Local fallback models:

```console
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:3b
```

## Setup

```console
uv python install 3.12
uv sync --frozen
```

Copy `.env.example` to `.env` only when shared local defaults are needed. Put
private machine-only secrets in ignored `.env.local`; the gateway loads `.env`
first and `.env.local` second, so `.env.local` can override local defaults.
Never put real keys in `.env.example`.

Important runtime settings:

- `LLM_GATEWAY_DATABASE_URL`
- `LLM_GATEWAY_REDIS_URL`
- `LLM_GATEWAY_OPENAI_API_KEY`
- `LLM_GATEWAY_GATEWAY_API_KEYS`
- `LLM_GATEWAY_OLLAMA_BASE_URL`

One-time local OpenAI key setup:

```powershell
Add-Content .env.local 'LLM_GATEWAY_OPENAI_API_KEY=your-real-openai-key'
```

Restart the gateway after changing `.env.local`.

## Run

```console
uv run llm-gateway
```

For local auto-reload, keep raw access logs disabled:

```console
uv run uvicorn llm_gateway.main:app --reload --no-access-log
```

HTTP endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /v1/generate`

Example generation request:

```json
{
  "model": "gateway-default",
  "input": "Respond with exactly: hello",
  "max_output_tokens": 32
}
```

## Database Migrations

The Alembic environment imports `llm_gateway.persistence.Base.metadata`.

```console
uv run alembic upgrade head
uv run alembic upgrade head --sql
uv run alembic revision --autogenerate -m "describe change"
```

The application ledger uses synchronous SQLAlchemy sessions. Configure its
runtime PostgreSQL URL as `postgresql+psycopg://user:password@host/database`.
A bare `postgresql://` URL is normalized to that driver.

## Quality Checks

```console
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run python -m alembic heads
uv run python -m alembic upgrade head --sql
```

Optional local integration checks:

```powershell
$env:LLM_GATEWAY_LOCAL_SMOKE="1"
uv run pytest tests/test_local_smoke.py -q

$env:LLM_GATEWAY_REAL_REDIS_TEST="1"
uv run pytest tests/test_real_redis.py -q
```

## Privacy

- Prompts and generated output are not logged or persisted.
- Provider errors are sanitized before reaching clients or persistence.
- API keys and provider secrets stay in environment configuration.
- Redis cache keys use scoped fingerprints, not raw prompt text.
- OpenAI requests use `store=false`.
- The packaged server disables Uvicorn raw access logs.
