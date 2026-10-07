"""Data-quality checks.

Each check is a SQL query that returns one row per problem it finds.

  error  The row is wrong and must not reach the warehouse. Error checks run on
         staging.flights and return `source_row`; those rows are copied to
         quarantine.flights with the name of the check they failed.
  warn   The row is usable but suspicious, or a table-level gap exists. The run
         continues and the count is recorded in meta.dq_results.

If the share of quarantined flights exceeds Config.max_reject_rate, the run stops
before the warehouse is rebuilt (a circuit breaker for a broken upstream file).
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from .config import Config

ERROR, WARN = "error", "warn"


@dataclass(frozen=True)
class Check:
    name: str
    severity: str
    description: str
    sql: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    description: str
    failed_count: int

    @property
    def passed(self) -> bool:
        return self.failed_count == 0


@dataclass(frozen=True)
class QualityOutcome:
    results: list[CheckResult]
    rows_checked: int
    rows_rejected: int

    @property
    def reject_rate(self) -> float:
        return self.rows_rejected / self.rows_checked if self.rows_checked else 0.0


class DataQualityError(RuntimeError):
    pass


# HHMM clock time -> minutes after midnight
def _minutes(col: str) -> str:
    return f"(({col} // 100) * 60 + {col} % 100)"


# Scheduled departure as a local timestamp. try_strptime returns NULL (instead of
# raising) for impossible dates, which valid_calendar_date reports separately.
_SCHEDULED_LOCAL = (
    "try_strptime(f.year || '-' || f.month || '-' || f.day || ' ' || "
    "(f.sched_dep_time // 100) || ':' || (f.sched_dep_time % 100), '%Y-%m-%d %H:%M')"
)


CHECKS: list[Check] = [
    # ---- error: quarantine the row ------------------------------------------------
    Check(
        "flight_key_not_null", ERROR,
        "Every flight has a date, carrier, flight number, origin, destination, and scheduled departure",
        """SELECT source_row FROM staging.flights
           WHERE year IS NULL OR month IS NULL OR day IS NULL OR carrier IS NULL
              OR flight IS NULL OR origin IS NULL OR dest IS NULL OR sched_dep_time IS NULL""",
    ),
    Check(
        "flight_key_unique", ERROR,
        "No two rows describe the same scheduled flight (the first is kept, later copies quarantined)",
        """SELECT source_row FROM (
               SELECT source_row, row_number() OVER (PARTITION BY flight_key ORDER BY source_row) AS n
               FROM staging.flights)
           WHERE n > 1""",
    ),
    Check(
        "valid_calendar_date", ERROR,
        "year/month/day form a real calendar date",
        """SELECT source_row FROM staging.flights
           WHERE try_strptime(year || '-' || month || '-' || day, '%Y-%m-%d') IS NULL""",
    ),
    Check(
        "valid_ranges", ERROR,
        "Clock times are real HHMM times; distance and air time are positive",
        """SELECT source_row FROM staging.flights
           WHERE sched_dep_time NOT BETWEEN 0 AND 2359 OR sched_dep_time % 100 >= 60
              OR (dep_time IS NOT NULL AND (dep_time NOT BETWEEN 0 AND 2400 OR dep_time % 100 >= 60))
              OR distance <= 0 OR air_time <= 0""",
    ),
    Check(
        "carrier_exists", ERROR,
        "Carrier code exists in the airlines table",
        """SELECT f.source_row FROM staging.flights f
           LEFT JOIN staging.airlines a ON a.carrier = f.carrier
           WHERE f.carrier IS NOT NULL AND a.carrier IS NULL""",
    ),
    Check(
        "airports_exist", ERROR,
        "Origin and destination exist in the airports table",
        """SELECT f.source_row FROM staging.flights f
           LEFT JOIN staging.airports o ON o.faa = f.origin
           LEFT JOIN staging.airports d ON d.faa = f.dest
           WHERE (f.origin IS NOT NULL AND o.faa IS NULL) OR (f.dest IS NOT NULL AND d.faa IS NULL)""",
    ),
    Check(
        "dep_delay_consistent", ERROR,
        "Scheduled departure + reported delay lands on the actual departure clock time "
        "(time-zone aware, so overnight delays across a daylight-saving change are handled)",
        # Plain HHMM arithmetic is off by an hour when a delay spans a DST switch. The exact
        # method (local time -> instant in the origin's time zone -> + delay -> back to local)
        # is ~40x slower, so it only runs on rows that could be affected:
        #   1. the fast integer check disagrees, or
        #   2. the delay window touches 00:00-04:00 local, when DST switches happen.
        # Every other row is decided by the fast check alone.
        f"""WITH departed AS (
                SELECT *,
                       {_minutes('f.sched_dep_time')} AS sched_min,
                       ({_minutes('f.dep_time')} - {_minutes('f.sched_dep_time')} - f.dep_delay::INTEGER) % 1440 = 0
                           AS clock_math_ok
                FROM staging.flights f
                WHERE f.dep_time IS NOT NULL),
            needs_tz AS (
                SELECT * FROM departed
                WHERE dep_delay IS NULL OR NOT clock_math_ok
                   OR sched_min < 240 OR sched_min + dep_delay >= 1440),
            tz_checked AS (
                SELECT f.source_row, f.dep_time, f.dep_delay,
                       timezone(a.tzone, timezone(a.tzone, {_SCHEDULED_LOCAL})
                                         + to_minutes(f.dep_delay::BIGINT)) AS actual_local
                FROM needs_tz f
                JOIN staging.airports a ON a.faa = f.origin
                WHERE a.tzone IS NOT NULL)
            SELECT source_row FROM tz_checked
            WHERE dep_delay IS NULL
               OR hour(actual_local) * 100 + minute(actual_local) <> dep_time % 2400""",
    ),
    # ---- warn: keep the row, record the count ---------------------------------------
    Check(
        "aircraft_in_registry", WARN,
        "Tail number appears in the aircraft registry (planes table)",
        """SELECT f.source_row FROM staging.flights f
           LEFT JOIN staging.planes p ON p.tailnum = f.tailnum
           WHERE f.tailnum IS NOT NULL AND p.tailnum IS NULL""",
    ),
    Check(
        "tailnum_present_if_departed", WARN,
        "Flights that departed have a tail number",
        "SELECT source_row FROM staging.flights WHERE dep_time IS NOT NULL AND tailnum IS NULL",
    ),
    Check(
        "weather_for_flight_hour", WARN,
        "An hourly weather reading exists for the flight's origin and scheduled hour",
        """SELECT f.source_row FROM staging.flights f
           LEFT JOIN staging.weather w ON w.origin = f.origin AND w.time_hour = f.time_hour
           WHERE w.origin IS NULL""",
    ),
    Check(
        "weather_hours_complete", WARN,
        "Each origin airport has a weather reading for every hour in the flight date range",
        """WITH hours AS (
               SELECT unnest(generate_series(
                   (SELECT date_trunc('day', min(time_hour)) FROM staging.flights),
                   (SELECT date_trunc('day', max(time_hour)) FROM staging.flights) + INTERVAL 23 HOUR,
                   INTERVAL 1 HOUR)) AS hour),
           origins AS (SELECT DISTINCT origin FROM staging.flights WHERE origin IS NOT NULL)
           SELECT o.origin, h.hour
           FROM origins o CROSS JOIN hours h
           LEFT JOIN staging.weather w ON w.origin = o.origin AND w.time_hour = h.hour
           WHERE w.origin IS NULL""",
    ),
    Check(
        "delay_under_24h", WARN,
        "Departure delay is under 24 hours (longer ones are real but rare)",
        "SELECT source_row FROM staging.flights WHERE dep_delay >= 1440",
    ),
]


def _volume_check(con: duckdb.DuckDBPyConnection, rows_now: int, config: Config) -> CheckResult:
    """Compare today's flight count with the last successful run."""
    prev = con.execute(
        """SELECT s.rows_in FROM meta.stage_metrics s
           JOIN meta.pipeline_runs r USING (run_id)
           WHERE r.status = 'succeeded' AND s.stage = 'quality'
           ORDER BY r.started_at DESC LIMIT 1"""
    ).fetchone()
    limit = config.max_volume_change
    failed = 0
    detail = f"Flight row count within {limit:.0%} of the last successful run"
    if prev and prev[0]:
        change = (rows_now - prev[0]) / prev[0]
        failed = int(abs(change) > limit)
        detail += f" (was {prev[0]:,}, now {rows_now:,}, {change:+.1%})"
    return CheckResult("volume_vs_last_run", WARN, detail, failed)


def run_quality(con: duckdb.DuckDBPyConnection, run_id: str, config: Config) -> QualityOutcome:
    con.execute("CREATE SCHEMA IF NOT EXISTS quarantine")
    con.execute(
        """CREATE TABLE IF NOT EXISTS quarantine.flights AS
           SELECT ''::VARCHAR AS run_id, ''::VARCHAR AS check_name, * FROM staging.flights WHERE false"""
    )
    rows_checked = con.execute("SELECT count(*) FROM staging.flights").fetchone()[0]

    results: list[CheckResult] = []
    for check in CHECKS:
        failing = f"({check.sql})"
        n = con.execute(f"SELECT count(*) FROM {failing}").fetchone()[0]
        if check.severity == ERROR and n:
            con.execute(
                f"""INSERT INTO quarantine.flights BY NAME
                    SELECT ? AS run_id, ? AS check_name, f.*
                    FROM staging.flights f
                    WHERE f.source_row IN (SELECT source_row FROM {failing})""",
                [run_id, check.name],
            )
        results.append(CheckResult(check.name, check.severity, check.description, n))
    results.append(_volume_check(con, rows_checked, config))

    # A row can fail several checks; it is rejected once.
    con.execute(
        """CREATE OR REPLACE TABLE staging.rejected_rows AS
           SELECT DISTINCT source_row FROM quarantine.flights WHERE run_id = ?""",
        [run_id],
    )
    rows_rejected = con.execute("SELECT count(*) FROM staging.rejected_rows").fetchone()[0]

    con.executemany(
        "INSERT INTO meta.dq_results VALUES (?, ?, ?, ?, ?, ?)",
        [[run_id, r.name, r.severity, r.failed_count, r.passed, r.description] for r in results],
    )

    outcome = QualityOutcome(results, rows_checked, rows_rejected)
    if outcome.reject_rate > config.max_reject_rate:
        raise DataQualityError(
            f"{rows_rejected:,} of {rows_checked:,} flights ({outcome.reject_rate:.2%}) failed error-level "
            f"checks; limit is {config.max_reject_rate:.2%}. Warehouse left unchanged."
        )
    return outcome
