# LLM Gateway

A privacy-conscious Python LLM gateway for authenticated text generation,
provider routing, usage accounting, quotas, caching, and local fallback models.

![LLM Gateway system design](docs/assets/system-design.svg)

## What It Does

- Exposes `POST /v1/generate` through FastAPI.
- Authenticates callers with gateway API keys.
- Resolves each key to one internal actor identity.
- Runs guardrails before quota, cache, or provider execution.
- Enforces per-actor Redis quotas.
- Serves actor-scoped encrypted Redis cache hits without new provider calls or
  charges.
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
- Provider failure writes no cache entry and no usage row.

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

Copy `.env.example` to `.env` only when local overrides are needed. The ignored
`.env` file is for local secrets; never put real keys in `.env.example`.

Important runtime settings:

- `LLM_GATEWAY_DATABASE_URL`
- `LLM_GATEWAY_REDIS_URL`
- `LLM_GATEWAY_OPENAI_API_KEY`
- `LLM_GATEWAY_GATEWAY_API_KEYS`
- `LLM_GATEWAY_OLLAMA_BASE_URL`

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
