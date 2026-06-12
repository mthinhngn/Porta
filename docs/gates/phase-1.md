# Phase 1 Gate Evidence

## Verdict

`CORE REVIEW: FAIL`

The Phase 1 verdict remains FAIL until every required command and independent
review below is recorded against the same pushed commit.

Focused working-tree results may be recorded below for handoff, but they are not
release evidence and cannot change this verdict.

## Candidate

- Failed candidate SHA A: `1167eb61636d964ff0cb8f75186a2fa159b06c64`
- Repaired candidate SHA: `caa9ed9921a8c5a76f3b67d67ee2715f12039246`
- Evidence SHA B: this evidence-only commit; verify its exact value with
  `git rev-parse HEAD` and `git ls-remote origin refs/heads/main`
- Branch: `main`
- Remote: private `mthinhngn/llm-gateway`

## Automated gate

| Check | Result | Evidence |
| --- | --- | --- |
| Frozen install | pass | 43 packages checked on repaired candidate and clean clone |
| Ruff lint | pass | `uv run ruff check .` |
| Ruff format | pass | 49 files formatted |
| Strict mypy | pass | 30 source files |
| Complete pytest | pass with live skips | `155 passed, 2 skipped`; only opt-in live tests skipped |
| Alembic head | pass | single `20260612_0003` head |
| Migration SQL | pass | complete upgrade and downgrade SQL; deduplication precedes uniqueness |
| Health probes | pass | packaged server returned live/ready and released its listener |
| Live OpenAI success | blocked | no replacement key configured |
| Live auth failure | pass | real OpenAI 401 mapped to sanitized gateway 502 |
| Secret/privacy scan | pass | no non-test credential-shaped values; query probe absent from logs |
| Clean clone | pass | detached repaired candidate repeated the complete non-paid gate |
| Private remote sync | pass | private repo; local, upstream, and remote SHA equal |

Windows gate commands set `UV_LINK_MODE=copy` and use a fresh local
`UV_CACHE_DIR` to avoid cloud-file hardlink failures.

## Focused working-tree evidence

Status: verified locally on June 12, 2026, without a candidate SHA. These facts
must be rerun by the main agent against the final evidence commit.

| Check | Result | Evidence |
| --- | --- | --- |
| Frozen install | verified, non-release | `uv sync --frozen`; 40 packages checked |
| Full Ruff lint | verified, non-release | `uv run ruff check .`; passed |
| Full Ruff format | verified, non-release | `uv run ruff format --check .`; 49 files formatted |
| Strict mypy | verified, non-release | `uv run mypy`; 30 source files passed |
| Complete pytest | verified, non-release | `145 passed, 2 skipped`; skips are the opt-in live tests |
| Owned focused tests | verified, non-release | `15 passed, 2 skipped`; skips are both opt-in live network tests |
| Owned Python Ruff lint | verified, non-release | `uv run ruff check tests/test_generate_api.py tests/test_migrations.py tests/test_live_smoke.py` |
| Owned Python Ruff format | verified, non-release | `uv run ruff format --check tests/test_generate_api.py tests/test_migrations.py tests/test_live_smoke.py` |
| Safe server construction | verified, non-release | packaged entry point sets `access_log=False`; reload command documents `--no-access-log` |
| Runtime PostgreSQL driver | verified, non-release | psycopg engine construction and pre-start rejection of runtime asyncpg URLs |
| Alembic single head | verified, non-release | `20260612_0003 (head)` |
| PostgreSQL offline SQL | verified, non-release | complete `0001 -> 0002 -> 0003` SQL rendered with transaction and version updates |
| Health probes | verified, non-release | real Uvicorn process returned `live` and `ready`; process exited and port closed |
| Live invalid-key probe | verified, non-release | OpenAI returned 401; gateway returned sanitized 502 with no usage row |
| Secret/privacy scan | verified, non-release | six credential-shaped matches, all deliberate test fixtures; `.env` ignored |
| Remote privacy | verified, non-release | GitHub repository is private; pre-candidate local/upstream/remote SHA matched |
| Paid live success | blocked | no `LLM_GATEWAY_OPENAI_API_KEY` entry detected in ignored `.env` |

## Failed candidate review

Candidate `1167eb61636d964ff0cb8f75186a2fa159b06c64` received
`CORE REVIEW: FAIL` from all three independent reviewers. The repair wave:

- rejects `max_output_tokens` below the OpenAI minimum before ledger/provider work
- reconciles ambiguous success commits without relabeling paid provider success as failure
- deduplicates legacy usage rows before adding the unique attempt constraint
- ignores future pricing snapshots during bootstrap comparison
- enforces requested-model and resolved-route consistency
- disables raw Uvicorn access logs in the packaged server entry point
- provides a supported synchronous psycopg runtime URL and driver

Repair working-tree verification on June 12, 2026:

- `155 passed, 2 skipped`; skips are the opt-in live probes
- Ruff lint and format passed
- strict mypy passed for 30 source files
- packaged-server credential-shaped query probe emitted no query or secret
- migration SQL placed deterministic duplicate cleanup before the unique constraint
- invalid-key OpenAI network probe passed with sanitized failure and no usage

The focused API tests cover timeout, authentication failure, malformed usage,
refusal, incomplete response, unexpected provider failure, and persistence
failure. They verify terminal request/attempt state, no usage on failure,
sanitized public and persisted errors, and absence of prompt, output, and API
key sentinels from all persisted table values.

## Required behavioral evidence

- uncached, partially cached, and fully cached cost calculations
- ten-decimal `Decimal` rounding
- atomic request and first-attempt creation
- rollback when atomic start fails
- one usage record per attempt
- duplicate completion rejection without state mutation
- malformed usage, refusal, incomplete response, timeout, and authentication errors
- sanitized client errors, logs, and persisted error summaries
- prompts and generated output absent from logs and persistence

## Independent reviews

### Provider and API

- Reviewer: independent read-only provider/API reviewer
- Reviewed SHA: `caa9ed9921a8c5a76f3b67d67ee2715f12039246`
- Verdict: `CORE REVIEW: FAIL`
- Findings: no code findings; paid success smoke blocked by missing replacement key
- Evidence: `90 passed` focused; `155 passed, 2 skipped` full; Ruff, format,
  mypy, migration SQL, and invalid-key live probe passed

### Ledger and migrations

- Reviewer: independent read-only ledger/migration reviewer
- Reviewed SHA: `caa9ed9921a8c5a76f3b67d67ee2715f12039246`
- Verdict: `CORE REVIEW: PASS`
- Findings: none
- Evidence: `48 passed` focused; full suite, Ruff, format, mypy, upgrade,
  downgrade, reconciliation mismatch probe, and clean state passed

### Privacy and release

- Reviewer: independent read-only privacy/release reviewer
- Reviewed SHA: `caa9ed9921a8c5a76f3b67d67ee2715f12039246`
- Verdict: `CORE REVIEW: FAIL`
- Findings: paid success smoke blocked; this evidence commit was still pending
- Evidence: clean-clone gate, packaged-server query privacy probe, psycopg
  runtime construction, secret scan, private remote, and exact SHA sync passed

## Live-call cost

- Approved maximum: USD 0.01
- Model: `gpt-4.1-mini`
- Configured preflight ceiling: USD 0.0001280000
- Paid live success result: pending
- Invalid-key live failure result: pass
- Actual recorded gateway cost: pending

## Finalization rule

After candidate SHA A passes review, reviewer summaries are committed as
evidence-only SHA B. The complete gate reruns on SHA B. This file may change to
`CORE REVIEW: PASS` only when SHA B is clean, pushed, private, synchronized, and
has no blocking findings.
