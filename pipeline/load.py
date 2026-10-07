"""Load: copy each landed Parquet file into a `staging` table in DuckDB.

Staging is rebuilt from scratch on every run, so re-running the pipeline never
double-loads anything. Flights also get a `flight_key`: a hash of the fields
that identify one scheduled flight, used to find duplicates and as the
warehouse primary key.
"""

from __future__ import annotations

import duckdb

from .config import Config

FLIGHT_NATURAL_KEY = ("year", "month", "day", "carrier", "flight", "origin", "sched_dep_time")


def _quote(path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def load_staging(con: duckdb.DuckDBPyConnection, config: Config) -> dict[str, int]:
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    counts: dict[str, int] = {}
    for table in config.tables:
        source = f"read_parquet({_quote(config.landing_dir / f'{table}.parquet')})"
        if table == "flights":
            key = ", ".join(FLIGHT_NATURAL_KEY)
            select = f"SELECT md5(concat_ws('|', {key})) AS flight_key, * FROM {source}"
        else:
            select = f"SELECT * FROM {source}"
        con.execute(f"CREATE OR REPLACE TABLE staging.{table} AS {select}")
        counts[table] = con.execute(f"SELECT count(*) FROM staging.{table}").fetchone()[0]
    return counts
