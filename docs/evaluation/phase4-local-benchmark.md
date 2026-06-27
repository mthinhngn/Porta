# Phase 4 Local Benchmark

The Phase 4 benchmark compares the real gateway `tier=standard` routing path
against the real `tier=auto` routing path using `GenerationService.generate`
and the versioned `src/llm_gateway/evaluation/fixtures/phase4_v1.json`
dataset. The default mode is local: it does not call provider APIs, does not
require API keys, and does not spend money.

Run locally from PowerShell:

```powershell
uv run python scripts/run_phase4_benchmark.py --mode local --report-path reports/phase4-benchmark.json
```

The report includes controls, scorer results, per-case result hashes, cost,
latency, and pass-rate deltas. The CLI exits nonzero if `tier=auto` has any
quality regression, missing approved case, worse pass rate, or no cost/latency
improvement.

Production `tier=auto` remains disabled unless
`LLM_GATEWAY_AUTO_ROUTING_ENABLED=true` is set. The local benchmark enables
auto routing only inside its isolated benchmark service.

Paid live mode is refused unless both opt-ins are present:

```powershell
$env:LLM_GATEWAY_PHASE4_PAID_LIVE = "1"
uv run python scripts/run_phase4_benchmark.py --mode paid-live --allow-paid-live --max-requests 24 --max-spend-usd 0.01
```

The benchmark also refuses execution when the projected request count exceeds
`--max-requests` or projected paid spend exceeds `--max-spend-usd`.
