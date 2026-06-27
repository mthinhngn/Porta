# Phase 4 Gate Evidence

## Verdict

Phase 4 evaluation and auto-routing implementation is ready for Phase 5.

`CORE REVIEW: PASS`

## Scope Reviewed

- Versioned `phase4-v1` evaluation dataset at
  `src/llm_gateway/evaluation/fixtures/phase4_v1.json`.
- Deterministic scoring in `src/llm_gateway/evaluation/scoring.py`.
- Phase 4 benchmark runner using the real `GenerationService.generate` service
  path for `tier=standard` versus `tier=auto`.
- Executable benchmark PASS rule: no quality regression, no missing approved
  cases, same or better pass rate, and better cost and/or latency.
- Config-gated production `tier=auto` with
  `LLM_GATEWAY_AUTO_ROUTING_ENABLED=false` by default.
- API, cache, provider allowlist, guardrail, quota, and ledger invariants from
  earlier phases.
- README, architecture, and benchmark runbook documentation.

## Repository Evidence

- Branch: `eval`.
- Reviewed artifact SHA:
  `f7f2c69b0b8813b199b52c7a22cb5bb7dab315e5`.
- Remote branch SHA verified for reviewed artifact:
  `f7f2c69b0b8813b199b52c7a22cb5bb7dab315e5`.
- Remote branch: `origin/eval`.
- Remote repo URL: `https://github.com/mthinhngn/Porta`.
- Remote repo name: `mthinhngn/Porta`.
- Remote clarification: this checkout intentionally uses the private
  `mthinhngn/Porta` repository as `origin` for the `llm-gateway` work.
- Remote visibility: `PRIVATE`.
- Clean worktree proof after reviewed artifact push: `git status --short`
  returned no output.

## Checks Run

Commands were run from `C:\Users\thinh\llm-gateway` on June 27, 2026
America/Los_Angeles.

| Check | Result | Evidence |
| --- | --- | --- |
| `uv run ruff format .` | PASS | `81 files left unchanged` on final run |
| `uv run ruff format --check .` | PASS | `81 files already formatted` |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run mypy` | PASS | `Success: no issues found in 49 source files` |
| `uv run pytest -q` | PASS | `254 passed, 7 skipped, 1 warning` |
| `uv run python -m alembic heads` | PASS | `20260612_0003 (head)` |
| `uv run python -m alembic upgrade head --sql` | PASS | SQL rendered through `20260612_0003` and `COMMIT` |
| `npm run build` | PASS | `tsc --noEmit && vite build`; Vite built `36 modules` |

## Benchmark Evidence

- Command:
  `uv run python scripts/run_phase4_benchmark.py --mode local --report-path reports/phase4-benchmark.json`
- Result: PASS.
- Saved report artifact: `reports/phase4-benchmark.json`.
- Report schema: `phase4-benchmark-report-v2`.
- Report ID: `ba4272c64b2ebbd4`.
- Report SHA256:
  `5EF7B70C52AA1E8F50CF88F638074829948B47C2D34AA60417DB730FD3221F61`.
- Dataset: `phase4-v1`.
- Dataset case count: `12`.
- Compared tiers: `tier=standard` and `tier=auto`.
- Service path: `GenerationService.generate`.
- Paid provider calls: none.
- Request count: `24`.

PASS rule evidence from the saved report:

- No quality regression: `true`.
- No missing cases: `true`.
- Same or better pass rate: `true`.
- Better cost or latency: `true`.
- Missing approved case IDs: `[]`.

Summary from the saved report:

- Standard cases: `12`; passed: `12`; failed: `0`.
- Auto cases: `12`; passed: `12`; failed: `0`.
- Standard average quality score: `1`.
- Auto average quality score: `1`.
- Standard total cost: `0.0004020000`.
- Auto total cost: `0`.
- Cost delta: `-0.0004020000`.
- Standard average latency: `32.33333333333333333333333333ms`.
- Auto average latency: `12.25ms`.
- Average latency delta: `-20.0833333333ms`.

## Privacy And Security Evidence

- Dataset fixtures are synthetic and validated for sensitive-looking content.
- Benchmark reports store prompt hashes and output hashes instead of raw prompts
  or generated outputs.
- Local benchmark mode uses deterministic local provider doubles and does not
  require API keys.
- Paid-live benchmark mode is refused unless both `--allow-paid-live` and
  `LLM_GATEWAY_PHASE4_PAID_LIVE=1` are present.
- Request and spend caps are enforced before paid-live execution.
- Production `tier=auto` is disabled by default and requires
  `LLM_GATEWAY_AUTO_ROUTING_ENABLED=true`.
- Disabled `tier=auto` returns `auto_routing_unavailable`.
- Cache namespace includes the auto-routing enabled flag and policy version.
- Provider allowlist, auth, guardrail, quota, and single-usage-row ledger
  behavior remain covered by tests.

## Skipped Or Deferred Checks

- Check: paid live provider benchmark.
- Reason: paid-live mode can incur provider cost and requires explicit opt-in.
- Risk: local evidence proves gateway routing behavior and deterministic
  scoring, but not live provider quality drift.
- Follow-up: run paid-live benchmark with a capped budget only after explicit
  approval.

- Check: live OpenAI smoke.
- Reason: opt-in live provider test requiring `LLM_GATEWAY_LIVE_SMOKE=1` and a
  real ignored `LLM_GATEWAY_OPENAI_API_KEY`.
- Risk: live OpenAI availability is not re-proven by this local gate.
- Follow-up: run only when live provider validation is explicitly requested.

- Check: local Ollama smoke.
- Reason: opt-in local model test requiring `LLM_GATEWAY_LOCAL_SMOKE=1` and
  local Ollama models.
- Risk: real local model availability is not re-proven by this deterministic
  gate.
- Follow-up: run when validating a workstation or container with Ollama.

- Check: real Redis concurrency tests.
- Reason: opt-in integration tests requiring `LLM_GATEWAY_REAL_REDIS_TEST=1`
  and a real Redis instance.
- Risk: in-process deterministic tests cover cache invariants; external Redis
  behavior is not re-proven here.
- Follow-up: run during Phase 5 container-stack validation.

## Review Findings

- Blocking: none.
- Non-blocking: paid-live, live OpenAI, local Ollama, and real Redis checks
  remain explicit opt-in validations.

## Next Phase Readiness

- PASS/FAIL: PASS.
- Next phase unlocked: Phase 5 Containers and Load.
