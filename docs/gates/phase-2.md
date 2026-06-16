# Phase 2 Gate Evidence

## Verdict

`CORE REVIEW: PASS`

Phase 2 code, local fallback smokes, Redis integration checks, and four
read-only review lanes passed on the pushed Phase 2 branch. The paid OpenAI
live success smoke remains pending explicit user approval and is not claimed as
passed in this record.

## Candidate

- Reviewed implementation SHA: `e7e917a413ce577f26af2b16aebf294d7ec35f3b`
- Branch: `codex/p2-secure-gateway`
- Remote: private `mthinhngn/llm-gateway`
- Provider scope: OpenAI primary plus local Ollama fallbacks
- Local fallback models: `llama3.2:3b` and `qwen2.5-coder:3b`
- Removed from current Phase 2 scope: Anthropic and Gemini cloud fallbacks

This closeout document is evidence-only. Verify the exact evidence commit with
`git rev-parse HEAD` and `git rev-parse origin/codex/p2-secure-gateway`.

## Verdict rule

Phase 2 requires the exact candidate commit to pass the automated gate, both
free local fallback smokes, Redis integration checks, and all read-only review
lanes. The paid OpenAI smoke is still an explicit-approval gate item and must
not be run or marked passed without user approval.

## Contract freeze

The implementation under review must match these frozen rules:

- `POST /v1/generate` requires `Authorization: Bearer <gateway_api_key>`.
- The request body remains the Phase 1 body; caller identity is not accepted in
  JSON.
- Final runtime order is `auth -> guardrail -> quota -> cache -> provider ->
  persist`.
- Cache scope is per actor and keyed from actor identity, normalized request
  fingerprint, resolved model, and guardrail version.
- Provider order is primary OpenAI, then task-aware local fallback through
  Ollama: Qwen then Llama for coding, or Llama then Qwen for general prompts.
- Retry policy allows at most one same-provider retry within one shared
  absolute deadline.
- Guardrail outcomes are only `allow` or `block`, with sanitized reason codes.
- One gateway request may have multiple attempts, but exactly one winning
  attempt may create one usage row and one charge.
- Local fallback pricing is zero USD while token accounting is retained.
- The Redis cache execution lock is fail-closed and owner-checked; stale
  non-expiring locks require operator cleanup rather than application takeover.

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
- coding fallback order is OpenAI, Qwen, then Llama
- general fallback order is OpenAI, Llama, then Qwen
- only OpenAI receives the one allowed same-provider retry
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

| Check | Result | Evidence |
| --- | --- | --- |
| Frozen install | pass | `uv sync --frozen` |
| Ruff lint | pass | `uv run ruff check .` |
| Ruff format | pass | `uv run ruff format --check .` |
| Strict mypy | pass | `uv run mypy` |
| Complete pytest | pass | `uv run pytest -q`; `200 passed, 7 skipped, 1 warning` |
| Alembic head | pass | `uv run python -m alembic heads`; single `20260612_0003 (head)` |
| Migration SQL | pass | `uv run python -m alembic upgrade head --sql` |

The gate must show exactly one Alembic head. Published revisions remain
immutable; any Phase 2 schema changes must be additive.

## Local and integration smoke

| Check | Result | Evidence |
| --- | --- | --- |
| Local Ollama fallback smoke | pass | `LLM_GATEWAY_LOCAL_SMOKE=1 uv run pytest tests/test_local_smoke.py -q`; `2 passed` |
| Real Redis integration smoke | pass | `LLM_GATEWAY_REAL_REDIS_TEST=1 uv run pytest tests/test_real_redis.py -q`; `3 passed` |
| Local general fallback | pass | deterministic retryable OpenAI failures fall back to Llama |
| Local coding fallback | pass | deterministic retryable OpenAI failures fall back to Qwen |
| Local accounting | pass | local winning attempts create one zero-cost usage row |
| Paid OpenAI success smoke | pending approval | not run in this closeout; do not claim pass before explicit approval |

Live smoke remains opt-in and gate-only. Required live evidence:

- free local general fallback succeeds with two deterministic retryable OpenAI
  failures followed by Llama
- free local coding fallback succeeds with two deterministic retryable OpenAI
  failures followed by Qwen
- both local winners create exactly one zero-cost usage row tied to the winning
  attempt
- authenticated low-cost OpenAI success still works after explicit approval

## Reviewer sign-off

Four read-only reviewers independently returned `CORE REVIEW: PASS`:

| Lane | Verdict | Evidence |
| --- | --- | --- |
| Auth/quota | `CORE REVIEW: PASS` | auth and quota short-circuits, no bypass, no downstream side effects |
| Cache isolation/concurrency | `CORE REVIEW: PASS` | actor-scoped cache, guarded concurrency, no cross-actor cache reuse |
| Retry/fallback/charging | `CORE REVIEW: PASS` | OpenAI retry once, task-aware fallback order, no double charging |
| Guardrail/privacy/Ollama/readiness | `CORE REVIEW: PASS` | block path privacy, sanitized errors, Ollama readiness/local fallback behavior |

Any unresolved bypass, leak, double-charge path, or fallback-order mismatch is
an automatic `CORE REVIEW: FAIL`.

## Phase 3 readiness

Phase 3 must not begin until this evidence commit is pushed, the final gate
passes again after the evidence update, and the closeout reviewers confirm the
exact pushed SHA. Phase 3 scope starts with observability only: Prometheus
metrics, authenticated usage analytics, Grafana dashboards, and alerts.
