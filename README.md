# LLM Gateway

Phase 1 foundation for a privacy-conscious, OpenAI-compatible LLM gateway.
This phase implements one narrow generation path:
application composition, `/v1/generate`, normalized provider boundaries,
Decimal cost accounting, persistence for requests and usage, and migrations.
The Phase 1 release gate remains open until the evidence in
`docs/gates/phase-1.md` is completed against one pushed commit.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```console
uv python install 3.12
uv sync --frozen
```

Copy `.env.example` to `.env` only when local overrides are needed. Local
health checks do not require database or provider credentials, but `/v1/generate`
needs a database URL and `LLM_GATEWAY_OPENAI_API_KEY`. The ignored `.env` file
is for local secrets; never put a real key in `.env.example`.

The application ledger uses synchronous SQLAlchemy sessions. Configure its
PostgreSQL URL as `postgresql+psycopg://user:password@host/database`. A bare
`postgresql://` URL is normalized to that driver. Runtime
`postgresql+asyncpg://` URLs are rejected before startup; asyncpg remains
installed only for Alembic's asynchronous migration environment.

The implementation has one configured OpenAI/model mapping. Authentication,
Redis, retries, fallback, dynamic routing, and additional providers are outside
Phase 1.

## Run

```console
uv run llm-gateway
```

The project entry point disables Uvicorn's raw access log because its request
target can contain confidential query-string values. The gateway still emits a
structured completion event using only the matched route template, method,
status, duration, and correlation ID.

For local auto-reload, preserve the same privacy control explicitly:

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

## Quality Checks

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Database Migrations

The Alembic environment imports `llm_gateway.persistence.Base.metadata`.
The current revisions create the Phase 1 persistence schema, including pricing
snapshots for Decimal cost accounting. The complete additive chain is
`20260611_0001 -> 20260611_0002 -> 20260612_0003`.

Alembic intentionally uses the separate `postgresql+asyncpg://` URL in
`alembic.ini` because `alembic/env.py` constructs an async migration engine.
That migration-only URL must not be copied into
`LLM_GATEWAY_DATABASE_URL`, which is consumed by the synchronous application
ledger.

Usage accounting prices uncached input, cached input, and output separately.
The checked-in `gpt-4.1-mini` defaults are:

- input: USD 0.40 per million tokens
- cached input: USD 0.10 per million tokens
- output: USD 1.60 per million tokens

These defaults matched the
[official GPT-4.1 mini model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
when checked on June 12, 2026. Recheck provider pricing before approving a live
release gate.

```console
uv run alembic upgrade head
uv run alembic upgrade head --sql
uv run alembic revision --autogenerate -m "describe change"
```

## Optional Live Smoke

The live smoke tests are opt-in because the success case calls the real OpenAI
API. Save the key in the ignored `.env` file:

```console
LLM_GATEWAY_OPENAI_API_KEY=...
```

Then explicitly enable the gate in the current PowerShell session:

```powershell
$env:LLM_GATEWAY_LIVE_SMOKE="1"
uv run pytest tests/test_live_smoke.py -s
```

The paid success probe pins `gpt-4.1-mini`, uses a 16-token output cap, and
checks a conservative USD 0.0001280000 estimated ceiling against the approved
USD 0.01 maximum. It prints only the numeric estimated cost and currency. The
same opt-in file also sends an invalid-key request and verifies the sanitized
authentication failure path. It never prints the configured key.

Gate evidence is recorded under `docs/gates/`.

## Local Ollama Fallbacks

Phase 2 uses two free local fallback models:

```powershell
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:3b
$env:LLM_GATEWAY_LOCAL_SMOKE="1"
uv run pytest tests/test_local_smoke.py -s
```

General prompts prefer Llama and coding prompts prefer Qwen after the single
OpenAI retry. Both local models retain token accounting but use zero USD pricing.
