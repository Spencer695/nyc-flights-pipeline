"""Transform: build the warehouse star schema from staging, atomically.

All SQL files in sql/transform/ run inside one transaction, followed by
reconciliation checks. The transaction commits only if every check passes, so
readers see either the previous warehouse or the complete new one, never a
half-built mix.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .quality import ERROR, CheckResult

SQL_DIR = Path(__file__).parent / "sql" / "transform"


class ReconciliationError(RuntimeError):
    pass


def _one(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]


def reconcile(con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    """Prove nothing was lost or invented between staging and the warehouse."""
    staged = _one(con, "SELECT count(*) FROM staging.flights")
    rejected = _one(con, "SELECT count(*) FROM staging.rejected_rows")
    fact = _one(con, "SELECT count(*) FROM warehouse.fact_flights")

    staged_miles = _one(
        con,
        """SELECT coalesce(sum(distance), 0)::BIGINT FROM staging.flights
           WHERE source_row NOT IN (SELECT source_row FROM staging.rejected_rows)""",
    )
    fact_miles = _one(con, "SELECT coalesce(sum(distance_mi), 0)::BIGINT FROM warehouse.fact_flights")
    rollup = _one(con, "SELECT coalesce(sum(scheduled_flights), 0)::BIGINT FROM warehouse.agg_daily_carrier")

    return [
        CheckResult("reconcile_row_counts", ERROR,
                    f"staged ({staged:,}) = warehouse ({fact:,}) + quarantined ({rejected:,})",
                    abs(staged - fact - rejected)),
        CheckResult("reconcile_distance", ERROR,
                    "Total miles in staging (excluding quarantined rows) equal total miles in the warehouse",
                    abs(staged_miles - fact_miles)),
        CheckResult("reconcile_daily_rollup", ERROR,
                    "Daily rollup flight counts add up to the fact table",
                    abs(rollup - fact)),
    ]


def _record(con: duckdb.DuckDBPyConnection, run_id: str, results: list[CheckResult]) -> None:
    con.executemany(
        "INSERT INTO meta.dq_results VALUES (?, ?, ?, ?, ?, ?)",
        [[run_id, r.name, r.severity, r.failed_count, r.passed, r.description] for r in results],
    )


def transform(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    """Rebuild the warehouse. Returns the number of rows in fact_flights."""
    con.execute("BEGIN TRANSACTION")
    try:
        for path in sorted(SQL_DIR.glob("*.sql")):
            con.execute(path.read_text())
        results = reconcile(con)
        failed = [r for r in results if not r.passed]
        if failed:
            raise ReconciliationError("; ".join(r.description for r in failed))
        fact_rows = _one(con, "SELECT count(*) FROM warehouse.fact_flights")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    _record(con, run_id, results)
    return fact_rows
