"""Command line: `python -m pipeline` runs the whole pipeline and prints a run summary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .run import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NYC 2023 flights ETL pipeline")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--refresh", action="store_true", help="re-download source files even if cached")
    parser.add_argument("--skip-extract", action="store_true", help="load from existing landing/ Parquet")
    parser.add_argument("--max-reject-rate", type=float, default=Config.max_reject_rate)
    args = parser.parse_args(argv)

    config = Config(data_dir=args.data_dir, max_reject_rate=args.max_reject_rate)
    summary = run_pipeline(config, extract_data=not args.skip_extract, refresh=args.refresh)

    print(f"\nRun {summary.run_id}: {summary.status.upper()}")
    print(f"\n{'stage':<10} {'status':<10} {'ms':>9} {'rows in':>10} {'rows out':>10}")
    for stage, status, ms, rows_in, rows_out in summary.stages:
        fmt = lambda n: f"{n:,}" if n is not None else "-"
        print(f"{stage:<10} {status:<10} {ms:>9,.0f} {fmt(rows_in):>10} {fmt(rows_out):>10}")

    if summary.quality:
        q = summary.quality
        print(f"\nData quality: {q.rows_rejected:,} of {q.rows_checked:,} flights quarantined ({q.reject_rate:.3%})")
        for r in q.results:
            mark = "ok  " if r.passed else ("QUAR" if r.severity == "error" else "WARN")
            print(f"  [{mark}] {r.name:<30} {r.failed_count:>8,}  {r.description}")
    if summary.fact_rows is not None:
        print(f"\nwarehouse.fact_flights: {summary.fact_rows:,} rows")
    if summary.error:
        print(f"\nERROR: {summary.error}", file=sys.stderr)
    return 0 if summary.status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
