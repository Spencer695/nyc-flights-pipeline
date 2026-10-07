"""Run telemetry: every run, stage, and data-quality result is written to the
`meta` schema in the warehouse, and every event is also logged as a JSON line.

That gives two ways to answer "what happened in last night's run?":
query meta.* with SQL, or grep the logs.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb

META_DDL = """
CREATE SCHEMA IF NOT EXISTS meta;
CREATE TABLE IF NOT EXISTS meta.pipeline_runs (
    run_id       VARCHAR PRIMARY KEY,
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP,
    status       VARCHAR NOT NULL,          -- running | succeeded | failed
    error        VARCHAR
);
CREATE TABLE IF NOT EXISTS meta.stage_metrics (
    run_id       VARCHAR NOT NULL,
    stage        VARCHAR NOT NULL,
    started_at   TIMESTAMP NOT NULL,
    duration_ms  DOUBLE NOT NULL,
    rows_in      BIGINT,
    rows_out     BIGINT,
    status       VARCHAR NOT NULL           -- succeeded | failed
);
CREATE TABLE IF NOT EXISTS meta.dq_results (
    run_id       VARCHAR NOT NULL,
    check_name   VARCHAR NOT NULL,
    severity     VARCHAR NOT NULL,          -- error | warn
    failed_count BIGINT NOT NULL,
    passed       BOOLEAN NOT NULL,
    description  VARCHAR NOT NULL
);
"""


def log(event: str, **fields) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **fields}
    print(json.dumps(record, default=str), file=sys.stderr)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class StageMetrics:
    name: str
    rows_in: int | None = None
    rows_out: int | None = None


@dataclass
class RunTracker:
    con: duckdb.DuckDBPyConnection
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        self.con.execute(META_DDL)

    @contextmanager
    def run(self):
        self.con.execute(
            "INSERT INTO meta.pipeline_runs VALUES (?, ?, NULL, 'running', NULL)", [self.run_id, utcnow()]
        )
        log("run_started", run_id=self.run_id)
        try:
            yield self
        except Exception as exc:
            self._finish("failed", f"{type(exc).__name__}: {exc}")
            raise
        self._finish("succeeded", None)

    def _finish(self, status: str, error: str | None) -> None:
        self.con.execute(
            "UPDATE meta.pipeline_runs SET finished_at = ?, status = ?, error = ? WHERE run_id = ?",
            [utcnow(), status, error, self.run_id],
        )
        log("run_finished", run_id=self.run_id, status=status, error=error)

    @contextmanager
    def stage(self, name: str):
        metrics = StageMetrics(name)
        started, t0 = utcnow(), time.perf_counter()
        status = "failed"
        try:
            yield metrics
            status = "succeeded"
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self.con.execute(
                "INSERT INTO meta.stage_metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
                [self.run_id, name, started, ms, metrics.rows_in, metrics.rows_out, status],
            )
            log("stage", run_id=self.run_id, stage=name, status=status, duration_ms=round(ms, 1),
                rows_in=metrics.rows_in, rows_out=metrics.rows_out)
