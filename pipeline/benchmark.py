"""Query-performance benchmark: the same SQL against five storage setups.

    python -m pipeline.benchmark            # 10x scale (~4.4M rows)
    python -m pipeline.benchmark --scale 1  # warehouse size (~435K rows)

Setups
  sqlite            row store, no indexes (every query is a full table scan)
  sqlite_indexed    row store + composite indexes on (carrier_code, flight_date) and (origin, flight_date)
  duckdb_table      column store, data loaded into DuckDB's own format
  duckdb_csv        DuckDB querying a CSV file directly
  duckdb_parquet    DuckDB querying a Parquet file directly

The flight table is copied `--scale` times (each copy gets a distinct key) so the
gaps between setups show up at a realistic size. Every query's results are
compared across setups before any timing is reported.
"""

from __future__ import annotations

import argparse
import os
import platform
import sqlite3
import statistics
import time
from pathlib import Path

import duckdb

from .config import Config
from .report import markdown_table

QUERIES = {
    "Q1 lookup: one airline, one day": """
        SELECT flight_number, origin, dest, dep_delay_min
        FROM bench
        WHERE carrier_code = 'AS' AND flight_date = '2023-07-04'
        ORDER BY flight_number, origin, dest, dep_delay_min""",
    "Q2 full aggregate: delay by airline and month": """
        SELECT carrier_code,
               CAST(substr(flight_date, 6, 2) AS INTEGER) AS month,
               COUNT(*) AS flights,
               ROUND(AVG(arr_delay_min), 2) AS avg_arr_delay,
               ROUND(100.0 * SUM(is_arr_delayed_15) / COUNT(is_arr_delayed_15), 2) AS late_pct
        FROM bench
        GROUP BY 1, 2
        ORDER BY 1, 2""",
    "Q3 filtered aggregate: JFK in July by hour": """
        SELECT sched_dep_hour, COUNT(*) AS flights, ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay
        FROM bench
        WHERE origin = 'JFK' AND flight_date BETWEEN '2023-07-01' AND '2023-07-31'
        GROUP BY 1
        ORDER BY 1""",
}

# Identical column types in every setup, so the comparison is about storage, not types.
BENCH_SELECT = """
    SELECT flight_key || '-' || copy AS flight_key,
           strftime(flight_date, '%Y-%m-%d') AS flight_date,
           carrier_code, flight_number, tailnum, origin, dest,
           sched_dep_hour, dep_delay_min, arr_delay_min, air_time_min, distance_mi,
           status, is_arr_delayed_15::INTEGER AS is_arr_delayed_15
    FROM warehouse.fact_flights, range({scale}) AS t(copy)
    ORDER BY copy, flight_date, carrier_code, flight_number
"""

SQLITE_INDEXES = [
    "CREATE INDEX idx_carrier_date ON bench (carrier_code, flight_date)",
    "CREATE INDEX idx_origin_date ON bench (origin, flight_date)",
]


def _time(fn, repeats: int) -> tuple[float, list]:
    result = fn()  # warm-up run (also the result we verify)
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), result


def _ms(value: float) -> str:
    return f"{value:.2f}" if value < 10 else f"{value:,.0f}"


def _same(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        for va, vb in zip(ra, rb):
            if isinstance(va, float) or isinstance(vb, float):
                if va is None or vb is None or abs(va - vb) > 0.011:
                    return False
            elif va != vb:
                return False
    return True


def build_inputs(config: Config, work: Path, scale: int) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    paths = {
        "csv": work / "bench.csv",
        "parquet": work / "bench.parquet",
        "duckdb": work / "bench.duckdb",
        "sqlite": work / "bench.sqlite",
    }
    for p in paths.values():
        p.unlink(missing_ok=True)

    src = duckdb.connect(str(config.db_path), read_only=True)
    select = BENCH_SELECT.format(scale=scale)
    src.execute(f"COPY ({select}) TO '{paths['csv']}' (HEADER)")
    src.execute(f"COPY ({select}) TO '{paths['parquet']}' (FORMAT parquet)")

    dst = duckdb.connect(str(paths["duckdb"]))
    dst.execute(f"CREATE TABLE bench AS SELECT * FROM read_parquet('{paths['parquet']}')")
    dst.close()

    lite = sqlite3.connect(paths["sqlite"])
    lite.execute(
        """CREATE TABLE bench (flight_key TEXT, flight_date TEXT, carrier_code TEXT, flight_number INTEGER,
           tailnum TEXT, origin TEXT, dest TEXT, sched_dep_hour INTEGER, dep_delay_min INTEGER,
           arr_delay_min INTEGER, air_time_min INTEGER, distance_mi INTEGER, status TEXT,
           is_arr_delayed_15 INTEGER)"""
    )
    cur = src.execute(select)
    while batch := cur.fetchmany(100_000):
        lite.executemany(f"INSERT INTO bench VALUES ({','.join('?' * 14)})", batch)
    lite.commit()
    lite.close()
    src.close()
    return paths


def run_benchmark(config: Config, scale: int, repeats: int) -> str:
    work = config.data_dir / "benchmark"
    t0 = time.perf_counter()
    paths = build_inputs(config, work, scale)
    build_s = time.perf_counter() - t0

    lite = sqlite3.connect(paths["sqlite"])
    rows = lite.execute("SELECT COUNT(*) FROM bench").fetchone()[0]

    sqlite_plans = {}
    sqlite_plans["before"] = lite.execute("EXPLAIN QUERY PLAN " + QUERIES["Q1 lookup: one airline, one day"]).fetchall()

    timings: dict[str, dict[str, float]] = {q: {} for q in QUERIES}
    results: dict[str, dict[str, list]] = {q: {} for q in QUERIES}

    def bench(setup: str, execute) -> None:
        for name, sql in QUERIES.items():
            ms, result = _time(lambda: execute(sql), repeats)
            timings[name][setup] = ms
            results[name][setup] = result

    bench("sqlite", lambda sql: lite.execute(sql).fetchall())

    t_idx = time.perf_counter()
    for ddl in SQLITE_INDEXES:
        lite.execute(ddl)
    lite.execute("ANALYZE")
    index_build_s = time.perf_counter() - t_idx
    sqlite_plans["after"] = lite.execute("EXPLAIN QUERY PLAN " + QUERIES["Q1 lookup: one airline, one day"]).fetchall()
    bench("sqlite_indexed", lambda sql: lite.execute(sql).fetchall())
    lite.close()

    duck = duckdb.connect(str(paths["duckdb"]), read_only=True)
    bench("duckdb_table", lambda sql: duck.execute(sql).fetchall())
    duck.close()

    for setup, view in [
        ("duckdb_csv", f"SELECT * FROM read_csv('{paths['csv']}', types={{'flight_date': 'VARCHAR'}})"),
        ("duckdb_parquet", f"SELECT * FROM read_parquet('{paths['parquet']}')"),
    ]:
        con = duckdb.connect()
        con.execute(f"CREATE VIEW bench AS {view}")
        bench(setup, lambda sql, con=con: con.execute(sql).fetchall())
        con.close()

    # Correctness first: every setup must return the same answer.
    for name, by_setup in results.items():
        baseline = by_setup["sqlite"]
        for setup, result in by_setup.items():
            if not _same(baseline, result):
                raise AssertionError(f"{name}: {setup} returned different results than sqlite")

    setups = ["sqlite", "sqlite_indexed", "duckdb_table", "duckdb_csv", "duckdb_parquet"]
    timing_rows = []
    for name in QUERIES:
        t = timings[name]
        fastest = min(t, key=t.get)
        timing_rows.append(
            (name, *[_ms(t[s]) for s in setups], f"{fastest} ({t['sqlite'] / t[fastest]:,.0f}x vs. sqlite)")
        )

    mb = lambda p: round(p.stat().st_size / 1e6, 1)
    size_rows = [
        ("CSV", mb(paths["csv"])),
        ("Parquet", mb(paths["parquet"])),
        ("DuckDB", mb(paths["duckdb"])),
        ("SQLite (with indexes)", mb(paths["sqlite"])),
    ]
    plan = lambda rows_: "; ".join(r[-1] for r in rows_)

    return "\n\n".join([
        "# Query Benchmark",
        f"{rows:,} rows ({scale}x the warehouse fact table). Median of {repeats} warm runs, in milliseconds. "
        f"Machine: {platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}, "
        f"DuckDB {duckdb.__version__}, SQLite {sqlite3.sqlite_version}. "
        "All five setups returned identical results for every query.",
        markdown_table(["query", *setups, "fastest"], timing_rows),
        "## Why the numbers look like this",
        "- **Q1 (selective lookup):** without an index SQLite reads every row. The composite index turns that "
        "into a direct seek. DuckDB has no B-tree index here but skips most of the file using per-block min/max stats.\n"
        "- **Q2 (scan everything, few columns):** a column store reads only the 4 columns the query needs "
        "and aggregates them in parallel; a row store reads every column of every row. Indexes cannot help a full scan.\n"
        "- **Q3 (filter + aggregate):** the index narrows SQLite to one airport-month, and DuckDB's column "
        "pruning plus block skipping does the same without one.\n"
        "- **CSV vs. Parquet:** the same engine is much slower on CSV because it has to parse text "
        "for every query, while Parquet is typed, compressed, and columnar.",
        "## SQLite query plan for Q1",
        f"- Before indexing: `{plan(sqlite_plans['before'])}`\n- After indexing: `{plan(sqlite_plans['after'])}`\n"
        f"- Building both indexes took {index_build_s:.1f} s.",
        "## Storage size",
        markdown_table(["format", "size_mb"], size_rows),
        f"Benchmark inputs took {build_s:.0f} s to build.",
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark row store vs. column store vs. file formats")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--scale", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("reports/BENCHMARK.md"))
    args = parser.parse_args()
    report = run_benchmark(Config(data_dir=args.data_dir), args.scale, args.repeats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(report)


if __name__ == "__main__":
    main()
