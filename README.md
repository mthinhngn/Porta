# LLM Gateway

Phase 1 foundation for a privacy-conscious, OpenAI-compatible LLM gateway.
This phase locks the core generation seam that Phase 2 can build on:
application composition, `/v1/generate`, normalized provider boundaries,
Decimal cost accounting, persistence for requests and usage, and migrations.

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
needs a database URL plus generation pricing and provider settings.

## Run

```console
uv run uvicorn llm_gateway.main:app --reload
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
snapshots for route-aware Decimal cost accounting.

```console
uv run alembic upgrade head
uv run alembic upgrade head --sql
uv run alembic revision --autogenerate -m "describe change"
```

## Optional Live Smoke

The live smoke tests are opt-in because they call the real OpenAI API.

```console
$env:OPENAI_API_KEY="..."
$env:LLM_GATEWAY_LIVE_SMOKE="1"
uv run pytest tests/test_live_smoke.py
```
