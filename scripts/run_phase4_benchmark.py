from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from llm_gateway.evaluation.benchmark import (
    DEFAULT_MAX_REQUESTS,
    DEFAULT_MAX_SPEND_USD,
    PAID_LIVE_ENV_FLAG,
    BenchmarkBudgetExceeded,
    BenchmarkConfig,
    BenchmarkPassRuleFailed,
    PaidLiveBenchmarkRefused,
    run_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 4 routing benchmark.")
    parser.add_argument(
        "--mode",
        choices=("local", "paid-live"),
        default="local",
        help="Default local mode uses deterministic fixtures and no network calls.",
    )
    parser.add_argument(
        "--allow-paid-live",
        action="store_true",
        help=f"Required with {PAID_LIVE_ENV_FLAG}=1 for paid live mode.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS,
        help="Hard cap on benchmark provider requests.",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=Decimal,
        default=DEFAULT_MAX_SPEND_USD,
        help="Hard cap on projected paid live spend.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("reports/phase4-benchmark.json"),
        help="JSON report output path.",
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=None,
        help="Optional Phase 4 v1 dataset path override.",
    )
    args = parser.parse_args()

    config = BenchmarkConfig(
        mode=args.mode,
        allow_paid_live=args.allow_paid_live,
        paid_live_env_value=os.getenv(PAID_LIVE_ENV_FLAG),
        max_requests=args.max_requests,
        max_spend_usd=args.max_spend_usd,
        report_path=args.report_path,
        dataset_path=args.dataset_path,
    )
    try:
        report = run_benchmark(config)
    except (BenchmarkBudgetExceeded, PaidLiveBenchmarkRefused) as exc:
        print(f"phase4 benchmark refused: {exc}")
        return 2
    except BenchmarkPassRuleFailed as exc:
        print(f"phase4 benchmark failed: {exc}")
        print(f"saved report: {args.report_path}")
        return 1

    print(json.dumps(report["pass_rule"], indent=2, sort_keys=True))
    print(f"wrote report: {args.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
