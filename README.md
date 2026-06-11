# LLM Gateway

Phase 0 foundation for a privacy-conscious, OpenAI-compatible LLM gateway.
This phase provides application composition, health checks, configuration,
structured logging, domain contracts, provider test doubles, persistence
metadata, and an initial database migration. It does not expose chat completion
or call an upstream provider.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```console
uv python install 3.12
uv sync --frozen
```

Copy `.env.example` to `.env` only when local overrides are needed. Phase 0
startup and readiness do not require database or provider credentials.

## Run

```console
uv run uvicorn llm_gateway.main:app --reload
```

Health endpoints:

- `GET /health/live`
- `GET /health/ready`

## Quality Checks

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Database Migrations

The Alembic environment imports `llm_gateway.persistence.Base.metadata`.
The initial revision creates the complete Phase 0 persistence schema.

```console
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe change"
```
