# Phase 2 Gate Criteria

## Verdict rule

Phase 2 is not complete until the exact candidate commit passes the automated
gate, the approved Anthropic fallback smoke, and all four read-only review
lanes.

## Contract freeze

The implementation under review must match these frozen rules:

- `POST /v1/generate` requires `Authorization: Bearer <gateway_api_key>`.
- The request body remains the Phase 1 body; caller identity is not accepted in
  JSON.
- Final runtime order is `auth -> guardrail -> quota -> cache -> provider ->
  persist`.
- Cache scope is per actor and keyed from actor identity, normalized request
  fingerprint, resolved model, and guardrail version.
- Provider order is primary OpenAI, then Anthropic, then Gemini when policy
  allows.
- Retry policy allows at most one same-provider retry within one shared
  absolute deadline.
- Guardrail outcomes are only `allow` or `block`, with sanitized reason codes.
- One gateway request may have multiple attempts, but exactly one winning
  attempt may create one usage row and one charge.

## Required PASS lanes

### 1. Auth and quota bypass

Must prove:

- missing, invalid, and disabled API keys fail before provider execution
- auth failures create zero provider-attempt rows and zero usage rows
- quota failures create zero provider calls and zero usage rows
- actor-scoped quota cannot be bypassed with concurrent requests

### 2. Cache isolation and concurrency

Must prove:

- same-actor identical requests can hit the cache
- different actors cannot reuse each other's cached responses
- blocked or failed requests never populate cache
- concurrent requests do not create cross-actor leaks or duplicate usage

### 3. Retry, fallback, and charging correctness

Must prove:

- non-retryable failures do not retry or fall back
- retryable failures use at most one same-provider retry
- fallback order is OpenAI, then Anthropic, then Gemini when enabled
- one shared deadline budget prevents attempts after time is exhausted
- exactly one winning attempt creates exactly one usage row and one charge

### 4. Guardrail and privacy behavior

Must prove:

- blocked requests stop before cache and provider execution
- blocked requests create zero usage and zero charges
- prompt text, output text, API keys, authorization headers, and provider
  secrets do not leak into logs, Redis, or persisted error fields
- sanitized client errors do not reveal provider internals

## Automated gate

- `uv sync --frozen`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy`
- `uv run pytest -q`
- `uv run python -m alembic heads`
- `uv run python -m alembic upgrade head --sql`

The gate must show exactly one Alembic head. Published revisions remain
immutable; any Phase 2 schema changes must be additive.

## Live smoke

Live smoke remains opt-in and gate-only.

Required live evidence:

- authenticated low-cost OpenAI success still works
- approved forced-fallback smoke shows a retryable OpenAI failure followed by
  successful Anthropic completion

Optional live evidence:

- Gemini smoke may run, but it is not Phase 2 blocking

## Reviewer sign-off

Four read-only reviewers must independently return `CORE REVIEW: PASS` on:

- auth/quota
- cache isolation/concurrency
- retry/fallback/charging
- guardrail/privacy

Any unresolved bypass, leak, double-charge path, or fallback-order mismatch is
an automatic `CORE REVIEW: FAIL`.
