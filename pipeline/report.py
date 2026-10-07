"""Reporting: run every query in sql/analytics/ against the warehouse and write
reports/REPORT.md, together with the latest run's data-quality results.

    python -m pipeline.report
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from .config import Config

SQL_DIR = Path(__file__).parent / "sql" / "analytics"


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return str(value)


def markdown_table(columns: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    lines += ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _header(sql: str, key: str) -> str:
    for line in sql.splitlines():
        if line.startswith(f"-- {key}:"):
            return line.split(":", 1)[1].strip()
    return ""


def run_query(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple]]:
    cur = con.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


def build_report(con: duckdb.DuckDBPyConnection) -> str:
    run_id, finished = con.execute(
        """SELECT run_id, finished_at FROM meta.pipeline_runs
           WHERE status = 'succeeded' ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    fact_rows = con.execute("SELECT count(*) FROM warehouse.fact_flights").fetchone()[0]

    parts = [
        "# NYC 2023 Flights: Pipeline Report",
        f"Generated from run `{run_id}` (finished {finished:%Y-%m-%d %H:%M} UTC). "
        f"Warehouse holds {fact_rows:,} flights.",
        "## Pipeline stages",
        markdown_table(*run_query(con, f"""
            SELECT stage, status, round(duration_ms)::INTEGER AS duration_ms, rows_in, rows_out
            FROM meta.stage_metrics WHERE run_id = '{run_id}' ORDER BY started_at""")),
        "## Data-quality results",
        "Error checks quarantine failing rows; warnings keep the row and record the count.",
        markdown_table(*run_query(con, f"""
            SELECT check_name AS "check", severity, failed_count AS rows_flagged, description
            FROM meta.dq_results WHERE run_id = '{run_id}'
            ORDER BY CASE severity WHEN 'error' THEN 0 ELSE 1 END, check_name""")),
    ]
    for path in sorted(SQL_DIR.glob("*.sql")):
        sql = path.read_text()
        parts += [f"## {_header(sql, 'title')}", _header(sql, "note"), markdown_table(*run_query(con, sql))]
    return "\n\n".join(p for p in parts if p) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write reports/REPORT.md from the warehouse")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("reports/REPORT.md"))
    args = parser.parse_args()
    con = duckdb.connect(str(Config(data_dir=args.data_dir).db_path), read_only=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(con))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
