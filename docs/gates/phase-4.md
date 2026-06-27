# Phase 4 Gate Evidence

## Verdict

Phase 4 evaluation and auto-routing implementation is ready for Phase 5 after
the evidence commit is pushed and the remote SHA is checked.

`CORE REVIEW: PASS`

## Scope Reviewed

- Versioned `phase4-v1` synthetic evaluation dataset and validation helpers.
- Deterministic scoring, report schema, and quality/cost/latency thresholds.
- Local-first benchmark runner with paid-live opt-in and budget controls.
- Optional `tier` generate request field with `standard` and `auto` modes.
- Evidence-gated auto-routing, provider allowlist handling, cache separation,
  fallback behavior, and single-usage-row ledger invariants.
- README, architecture, and benchmark runbook documentation.

## Repository Evidence

- Branch: `eval`
- Reviewed SHA: final commit containing this evidence file.
- Remote branch: `origin/eval`
- Worktree state before final commit: Phase 4 implementation changes present;
  final review must confirm clean state after commit.
- Remote visibility: `PRIVATE`

## Checks Run

Commands were run from `C:\Users\thinh\llm-gateway` on June 26, 2026
America/Los_Angeles.

| Check | Result | Evidence |
| --- | --- | --- |
| `uv sync --frozen` | PASS | `Checked 50 packages in 13ms` |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run ruff format --check .` | PASS | `81 files already formatted` after formatting Phase 4 files |
| `uv run mypy` | PASS | `Success: no issues found in 49 source files` |
| `uv run pytest -q` | PASS | `252 passed, 7 skipped, 1 warning` |
| `uv run python -m alembic heads` | PASS | `20260612_0003 (head)` |
| `uv run python -m alembic upgrade head --sql` | PASS | SQL rendered through `20260612_0003` and `COMMIT` |
| `npm run build` | SKIPPED | No frontend files changed in Phase 4 |

## Runtime Evidence

- Local benchmark command:
  `uv run python scripts/run_phase4_benchmark.py --mode local --report-path reports/phase4-benchmark.json`
- Benchmark mode: local deterministic fixtures only.
- Paid provider calls: none.
- Baseline policy: OpenAI-first deterministic routing.
- Candidate policy: local-first `candidate_auto` policy from accepted Phase 4
  evidence.
- Baseline pass rate: `1.0000000000`.
- Candidate auto pass rate: `1.0000000000`.
- Candidate auto per-case score delta: non-negative for every benchmark case.
- Baseline total cost: `0.0000254000`.
- Candidate auto total cost: `0.0000000000`.
- Baseline mean latency: `648ms`.
- Candidate auto mean latency: `216ms`.
- Report artifact: `reports/phase4-benchmark.json`.

## Privacy And Security Evidence

- Dataset fixtures are synthetic and validated for sensitive-looking content.
- Benchmark reports store prompt hashes and output hashes, not raw prompts or
  generated outputs.
- Paid live benchmarks are refused unless both `--allow-paid-live` and
  `LLM_GATEWAY_PHASE4_PAID_LIVE=1` are present.
- Request and spend caps are enforced before paid-live execution.
- `tier=auto` preserves auth, guardrail, quota, provider allowlist, cache,
  metrics, and ledger ordering.
- Cache keys separate `standard` and `auto` tiers.
- Metrics and analytics surfaces are unchanged and remain covered by existing
  privacy tests.

## Skipped Or Deferred Checks

- Check: paid live provider benchmark.
- Reason: Phase 4 default is local-first and paid live checks need explicit
  approval.
- Risk: local evidence proves routing policy behavior but not live provider
  quality drift.
- Follow-up: run paid live benchmark with a capped budget only when explicitly
  approved.

- Check: frontend production build.
- Reason: Phase 4 changed backend, evaluation, docs, scripts, and reports only.
- Risk: low; no frontend source files changed.
- Follow-up: run `npm run build` when frontend files are touched.

## Review Findings

- Blocking: none after final commit, push, clean worktree, and remote SHA
  verification pass.
- Non-blocking: live OpenAI, local Ollama, and real Redis smokes remain opt-in
  integration checks.

## Next Phase Readiness

- PASS/FAIL: PASS.
- Next phase unlocked: Phase 5 Containers and Load only after
  `CORE REVIEW: PASS` is recorded for the pushed Phase 4 evidence commit.
