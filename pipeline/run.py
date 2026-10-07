"""Orchestration: extract -> load -> quality -> transform, with telemetry around each stage."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from .config import Config
from .extract import extract
from .load import load_staging
from .quality import QualityOutcome, run_quality
from .telemetry import RunTracker
from .transform import transform


@dataclass
class RunSummary:
    run_id: str
    status: str
    error: str | None
    stages: list[tuple]           # (stage, status, duration_ms, rows_in, rows_out)
    quality: QualityOutcome | None
    fact_rows: int | None


def run_pipeline(config: Config, *, extract_data: bool = True, refresh: bool = False) -> RunSummary:
    """Run every stage once. Never raises for pipeline failures: the summary says what happened,
    and the same facts are in meta.pipeline_runs / meta.stage_metrics / meta.dq_results."""
    config.ensure_dirs()
    con = duckdb.connect(str(config.db_path))
    tracker = RunTracker(con)
    quality: QualityOutcome | None = None
    fact_rows: int | None = None
    status, error = "succeeded", None
    try:
        with tracker.run():
            if extract_data:
                with tracker.stage("extract") as st:
                    st.rows_out = sum(extract(config, refresh=refresh).values())

            with tracker.stage("load") as st:
                counts = load_staging(con, config)
                st.rows_out = sum(counts.values())

            with tracker.stage("quality") as st:
                st.rows_in = counts["flights"]
                quality = run_quality(con, tracker.run_id, config)
                st.rows_out = quality.rows_checked - quality.rows_rejected

            with tracker.stage("transform") as st:
                st.rows_in = quality.rows_checked - quality.rows_rejected
                fact_rows = transform(con, tracker.run_id)
                st.rows_out = fact_rows
    except Exception as exc:  # recorded by the tracker; reported in the summary
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        stages = con.execute(
            """SELECT stage, status, duration_ms, rows_in, rows_out FROM meta.stage_metrics
               WHERE run_id = ? ORDER BY started_at""",
            [tracker.run_id],
        ).fetchall()
        con.close()
    return RunSummary(tracker.run_id, status, error, stages, quality, fact_rows)
