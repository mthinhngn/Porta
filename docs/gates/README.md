# Phase Gate Review Process

This folder is the release-control record for the LLM Gateway phase plan. Each
phase must have a gate file named `phase-N.md`, and the next phase may start
only after the Review Manager records `CORE REVIEW: PASS` for the exact
committed SHA.

Implementation being present is not enough. A phase is complete only when the
code, tests, evidence, repository state, and review verdict all match the next
phase's entry requirements.

## Required Files

Every phase must create or update exactly one official gate file:

| Phase | Gate file | Next phase unlocked |
| --- | --- | --- |
| 0. Foundation | `docs/gates/phase-0.md` | Phase 1 Core Generation |
| 1. Core Generation | `docs/gates/phase-1.md` | Phase 2 Secure Multi-Provider |
| 2. Secure Multi-Provider | `docs/gates/phase-2.md` | Phase 3 Observability |
| 3. Observability | `docs/gates/phase-3.md` | Phase 4 Evaluation and Auto-Routing |
| 4. Evaluation and Auto-Routing | `docs/gates/phase-4.md` | Phase 5 Containers and Load |
| 5. Containers and Load | `docs/gates/phase-5.md` | Phase 6 Kubernetes and Helm |
| 6. Kubernetes and Helm | `docs/gates/phase-6.md` | Phase 7 CI/CD and Security |
| 7. CI/CD and Security | `docs/gates/phase-7.md` | Phase 8 Portfolio Release |
| 8. Portfolio Release | `docs/gates/phase-8.md` | `v1.0.0` release |

Readiness reports, repair notes, and step notes may exist, but they do not
replace the official `phase-N.md` gate file.

## Universal Reviewer Workflow

The Review Manager must run the same workflow for every phase:

1. Inspect the live repository state.
2. Confirm the current phase and exact SHA under review.
3. Confirm the worktree is clean before final PASS.
4. Confirm local `HEAD` matches the pushed remote SHA.
5. Confirm the repository is private until the final release PASS.
6. Run the required checks for the phase.
7. Review privacy and sensitive-data handling.
8. Verify `docs/gates/phase-N.md` contains complete evidence.
9. Return `CORE REVIEW: FAIL` if anything blocks promotion.
10. Return `CORE REVIEW: PASS` only when all requirements pass.

The reviewer must be read-only unless the user explicitly asks for repair
coordination. If the review fails, builders repair the phase, commit a new SHA,
push it, and the Review Manager reruns the full gate from the beginning.

## Universal PASS Requirements

Every phase must satisfy these requirements:

- `git status --short --branch` shows a clean worktree.
- `git rev-parse HEAD` equals the SHA recorded in the phase gate file.
- `git rev-parse origin/main` or the approved release branch equals the reviewed
  SHA.
- The remote repository is private until Phase 8 final release approval.
- No secrets, API keys, prompts, generated outputs, provider raw responses, or
  credentials are committed or logged.
- The phase gate file records commands, results, skipped checks, reasons for
  skipped checks, runtime evidence, known limitations, and reviewer verdict.
- All required tests/checks pass.
- Any opt-in live checks are either run with approval or explicitly deferred in
  the gate evidence.

## Baseline Checks

Run these checks unless the phase gate explains why a check is not applicable:

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run python -m alembic heads
uv run python -m alembic upgrade head --sql
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

If frontend code is part of the phase, also run:

```powershell
npm install
npm run build
```

If containers, Kubernetes, or CI assets are part of the phase, add the
phase-specific checks listed below.

## Phase 0: Foundation Gate

Purpose: prove the project foundation is safe enough for generation work.

Required evidence:

- FastAPI app boots.
- `GET /health/live` returns `200 {"status":"live"}`.
- `GET /health/ready` returns `200 {"status":"ready"}` without requiring
  provider credentials.
- Settings validation works for required runtime configuration.
- SQLAlchemy metadata and the initial Alembic migration are valid.
- Errors are sanitized and include correlation IDs where expected.
- Logs do not include prompts, completions, auth headers, provider secrets, or
  credentials.
- Ignore rules exclude local secrets, local databases, caches, virtualenvs, and
  build outputs.

PASS unlocks: Phase 1 Core Generation.

## Phase 1: Core Generation Gate

Purpose: prove one OpenAI-backed `POST /v1/generate` path works with correct
usage and cost accounting.

Required evidence:

- `POST /v1/generate` request and response contracts are stable.
- OpenAI provider sends `store=false`.
- Provider usage is normalized into input, cached input, output, and total
  token counts.
- Decimal cost calculation is exact and tied to pricing snapshots.
- Successful generation creates one request row, one winning attempt row, and
  one usage row.
- Failed provider attempts persist sanitized errors and do not create usage rows
  or charges.
- Migration SQL is valid.
- Mocked success, failure, timeout, malformed response, and privacy tests pass.
- Low-cost OpenAI live smoke is run only with approval, or explicitly deferred.

PASS unlocks: Phase 2 Secure Multi-Provider.

## Phase 2: Secure Multi-Provider Gate

Purpose: prove auth, quota, cache, guardrail, retry, and fallback controls are
safe under success, failure, and concurrency.

Required evidence:

- API-key auth maps each key to one actor identity.
- Disabled or invalid keys cannot reach guardrails, quota, cache, providers, or
  persistence.
- Provider allowlists cannot be bypassed by cache hits or fallback routing.
- Guardrails run before quota, cache, provider calls, usage rows, and charges.
- Quotas are actor-scoped and atomic under concurrency.
- Cache keys are actor-scoped and policy-scoped, and cache values are encrypted.
- Concurrent identical cache misses do not produce duplicate provider calls or
  duplicate usage rows.
- Retry/fallback behavior uses one shared deadline and does not double charge.
- Failed requests persist failed attempts without writing usage rows or cache
  values.
- Approved live provider fallback smoke is run or explicitly deferred.

PASS unlocks: Phase 3 Observability.

## Phase 3: Observability Gate

Purpose: prove operators can measure and diagnose the gateway without leaking
sensitive content.

Required evidence:

- `GET /metrics` returns Prometheus text.
- Metrics cover HTTP traffic, auth, guardrails, quota, cache, generation,
  provider attempts, latency, and ledger operations.
- Metric labels are bounded and low-cardinality.
- Metrics do not contain prompts, generated outputs, API keys, raw actors,
  provider secrets, or raw provider IDs.
- `GET /v1/analytics/usage/summary` is authenticated and admin-only.
- Analytics totals reconcile with SQL ledger usage rows.
- Grafana dashboard JSON parses and references real metric families.
- Prometheus alert rules parse and reference real metric families.
- Runbook covers expected failure modes and links to shipped artifacts.
- Frontend/operator console builds if it is included in Phase 3 scope.

PASS unlocks: Phase 4 Evaluation and Auto-Routing.

## Phase 4: Evaluation and Auto-Routing Gate

Purpose: prove routing changes are based on reproducible evaluation evidence.

Required evidence:

- Evaluation dataset format is versioned and documented.
- Dataset examples cover the target task categories.
- Scorers are deterministic or have documented variance controls.
- Evaluation reports are saved and reproducible.
- Paid benchmarks run only after local harness checks pass.
- Benchmark cost controls are documented.
- `tier=auto` or equivalent auto-routing behavior has regression tests.
- Auto-routing outperforms a documented baseline on the approved metric, or it
  is not enabled.
- Auto-routing preserves auth, quota, cache, guardrail, privacy, and ledger
  invariants from earlier phases.

PASS unlocks: Phase 5 Containers and Load.

## Phase 5: Containers and Load Gate

Purpose: prove the gateway can run as a production-style container stack under
load and failure.

Required evidence:

- Production image builds from a clean checkout.
- Image does not include local secrets, virtualenvs, caches, or test databases.
- Compose stack starts from scratch.
- Migrations run cleanly in the stack.
- Readiness and liveness behavior work inside containers.
- Redis, database, provider, and cache failure scenarios are tested.
- Graceful shutdown does not corrupt ledger or cache state.
- Sustained-load results are saved with target RPS, latency, error rate, and
  resource usage.
- Throttling or degradation behavior is documented.

Suggested checks:

```powershell
docker build .
docker compose up --build
docker compose ps
docker compose logs
```

PASS unlocks: Phase 6 Kubernetes and Helm.

## Phase 6: Kubernetes and Helm Gate

Purpose: prove deployment is repeatable, configurable, scalable, and rollback
safe in Kubernetes.

Required evidence:

- A clean kind cluster can install the Helm chart.
- Helm values validate required config and reject unsafe missing config.
- Secrets are not committed and are mounted or injected safely.
- Pods pass readiness and liveness probes.
- Migrations run in the intended deployment flow.
- Pod replacement does not break readiness or persistence.
- Disruption protection is configured where applicable.
- Prometheus Adapter or equivalent metrics path supports RPS-based HPA.
- HPA scaling evidence is saved.
- Rollback succeeds.
- Teardown removes created resources from a clean environment.

Suggested checks:

```powershell
helm lint <chart>
helm template <release> <chart>
kind create cluster
helm install <release> <chart>
kubectl get pods
kubectl rollout status deployment/<name>
helm rollback <release> <revision>
kind delete cluster
```

PASS unlocks: Phase 7 CI/CD and Security.

## Phase 7: CI/CD and Security Gate

Purpose: prove trusted automation can enforce quality, security, images, Helm,
and ephemeral deployment checks.

Required evidence:

- GitHub Actions quality workflow runs lint, format, mypy, tests, and migration
  checks.
- Security workflow runs dependency, secret, and image scans.
- Workflow permissions are least-privilege.
- Forked pull requests cannot access secrets.
- Images are tagged and published only from trusted refs.
- Helm validation runs in CI.
- Ephemeral Kubernetes verification runs in CI or is documented with a trusted
  manual substitute.
- CI evidence links or logs are recorded in `phase-7.md`.

PASS unlocks: Phase 8 Portfolio Release.

## Phase 8: Portfolio Release Gate

Purpose: prove the repository is safe and credible for public release and
`v1.0.0`.

Required evidence:

- Clean-machine setup works from public documentation.
- Every public claim has evidence in gate files, docs, tests, logs, screenshots,
  or benchmark reports.
- No secrets, private prompts, private outputs, credentials, local-only paths,
  or sensitive logs are exposed.
- All earlier phase gate files remain valid for the release SHA, or differences
  are documented and revalidated.
- README, architecture docs, runbooks, API examples, and interview/demo material
  are consistent.
- Release notes are ready.
- `v1.0.0` tag target SHA is recorded.
- The final Review Manager approves making the repo public.

PASS unlocks: final release and public portfolio publishing.

## Required Gate File Template

Each `phase-N.md` should use this structure:

```markdown
# Phase N Gate Evidence

## Verdict

CORE REVIEW: PASS or CORE REVIEW: FAIL

## Scope Reviewed

- ...

## Repository Evidence

- Branch:
- Reviewed SHA:
- Remote SHA:
- Worktree state:
- Remote visibility:

## Checks Run

| Check | Result | Evidence |
| --- | --- | --- |
| ... | PASS/FAIL | ... |

## Runtime Evidence

- ...

## Privacy And Security Evidence

- ...

## Skipped Or Deferred Checks

- Check:
- Reason:
- Risk:
- Follow-up:

## Review Findings

- Blocking:
- Non-blocking:

## Next Phase Readiness

- PASS/FAIL:
- Next phase unlocked:
```

## FAIL Handling

If any required item fails:

1. Record `CORE REVIEW: FAIL`.
2. List exact blockers with file paths, command output, and expected fix.
3. Stop promotion to the next phase.
4. Assign builders to repair only the failed phase scope.
5. Commit repairs in a new SHA.
6. Push the new SHA.
7. Rerun the complete review gate, not only the failed command.

Never carry a partial PASS into the next phase.
