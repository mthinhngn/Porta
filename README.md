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

The implementation has one configured OpenAI/model mapping. Authentication,
Redis, retries, fallback, dynamic routing, and additional providers are outside
Phase 1.

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
snapshots for Decimal cost accounting. The complete additive chain is
`20260611_0001 -> 20260611_0002 -> 20260612_0003`.

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
