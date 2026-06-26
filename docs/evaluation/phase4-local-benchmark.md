# Phase 4 Local Benchmark

The Phase 4 benchmark compares the current baseline routing policy against a
candidate auto policy with deterministic fixtures. The default mode is local:
it does not call provider APIs, does not require API keys, and does not spend
money.

Run locally from PowerShell:

```powershell
uv run python scripts/run_phase4_benchmark.py --mode local --report-path reports/phase4-benchmark.json
```

The report includes controls, policy summaries, per-case result hashes, cost,
latency, and pass-rate deltas. Report contents are deterministic so local runs
and CI can diff output safely.

Paid live mode is refused unless both opt-ins are present:

```powershell
$env:LLM_GATEWAY_PHASE4_PAID_LIVE = "1"
uv run python scripts/run_phase4_benchmark.py --mode paid-live --allow-paid-live --max-requests 20 --max-spend-usd 0.01
```

The benchmark also refuses execution when the projected request count exceeds
`--max-requests` or projected paid spend exceeds `--max-spend-usd`.
