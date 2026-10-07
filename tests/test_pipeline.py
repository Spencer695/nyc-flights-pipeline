from __future__ import annotations

from dataclasses import replace

import pytest

import pipeline.transform as transform_module
from pipeline.quality import CheckResult
from pipeline.run import run_pipeline

from conftest import flight, write_landing

GOOD = [
    flight(flight=100),
    flight(flight=200, carrier="DL", tailnum="N2", origin="LGA", dest="ROC", distance=254.0, air_time=60.0),
    flight(flight=300, carrier="B6", day=2),
]


def run(config, **kwargs):
    return run_pipeline(config, extract_data=False, **kwargs)


def dq(query, check_name):
    return query(
        """SELECT failed_count FROM meta.dq_results
           WHERE check_name = ? ORDER BY rowid DESC LIMIT 1""",
        [check_name],
    )[0][0]


# ---- happy path -----------------------------------------------------------------------

def test_clean_data_reaches_the_warehouse(config, query):
    write_landing(config.data_dir, GOOD)
    summary = run(config)

    assert summary.status == "succeeded", summary.error
    assert summary.fact_rows == 3
    assert query("SELECT count(*) FROM quarantine.flights") == [(0,)]
    assert [s[0] for s in summary.stages] == ["load", "quality", "transform"]
    assert all(s[1] == "succeeded" for s in summary.stages)


def test_rerunning_is_idempotent(config, query):
    write_landing(config.data_dir, GOOD)
    run(config)
    first = query("SELECT * FROM warehouse.fact_flights ORDER BY flight_key")
    run(config)
    second = query("SELECT * FROM warehouse.fact_flights ORDER BY flight_key")

    assert first == second
    assert query("SELECT count(*) FROM meta.pipeline_runs WHERE status = 'succeeded'") == [(2,)]


def test_status_and_late_flag_are_derived(config, query):
    write_landing(config.data_dir, [
        flight(flight=1),                                                            # on time
        flight(flight=2, arr_delay=20.0, arr_time=1215),                             # arrived late
        flight(flight=3, dep_time=None, dep_delay=None, arr_time=None, arr_delay=None, air_time=None),  # cancelled
        flight(flight=4, arr_time=None, arr_delay=None, air_time=None),              # departed, never arrived
    ])
    run(config)
    rows = query("SELECT flight_number, status, is_arr_delayed_15 FROM warehouse.fact_flights ORDER BY 1")
    assert rows == [(1, "completed", False), (2, "completed", True), (3, "cancelled", None), (4, "no_arrival", None)]


# ---- error checks quarantine bad rows ---------------------------------------------------

@pytest.mark.parametrize("bad_row, expected_check", [
    (flight(flight=999, carrier="ZZ"), "carrier_exists"),
    (flight(flight=999, dest="XXX"), "airports_exist"),
    (flight(flight=999, carrier=None), "flight_key_not_null"),
    (flight(flight=999, month=2, day=30), "valid_calendar_date"),
    (flight(flight=999, sched_dep_time=960, dep_time=1000, dep_delay=0.0), "valid_ranges"),
    (flight(flight=999, distance=-5.0), "valid_ranges"),
    (flight(flight=999, dep_time=905, dep_delay=30.0), "dep_delay_consistent"),
    (flight(flight=100), "flight_key_unique"),  # same key as GOOD[0]
])
def test_error_checks_quarantine_bad_rows(config, query, bad_row, expected_check):
    write_landing(config.data_dir, GOOD + [bad_row])
    summary = run(replace(config, max_reject_rate=1.0))  # allow the run to finish so we can inspect it

    assert summary.status == "succeeded", summary.error
    assert summary.fact_rows == 3
    quarantined = query("SELECT source_row, check_name FROM quarantine.flights")
    assert (4, expected_check) in quarantined
    assert query("SELECT count(*) FROM warehouse.fact_flights WHERE source_row = 4") == [(0,)]
    assert dq(query, "reconcile_row_counts") == 0


def test_delay_past_midnight_is_valid(config, query):
    write_landing(config.data_dir, [flight(sched_dep_time=2300, dep_time=30, dep_delay=90.0)])
    summary = run(config)
    assert summary.fact_rows == 1
    assert dq(query, "dep_delay_consistent") == 0


def test_daylight_saving_overnight_delays(config, query):
    """Real cases from the 2023 data. Delays are elapsed minutes, so a delay that spans a DST
    switch differs from plain clock arithmetic by 60 minutes, and that is correct."""
    write_landing(config.data_dir, [
        # Fall back (Nov 5): 22:50 EDT -> 06:44 EST is 534 elapsed minutes. Valid.
        flight(flight=1, year=2023, month=11, day=4, sched_dep_time=2250, dep_time=644, dep_delay=534.0, dest="ROC"),
        # Spring forward (Mar 12): 21:27 EST -> 07:55 EDT is 568 elapsed minutes. Valid.
        flight(flight=2, year=2023, month=3, day=11, sched_dep_time=2127, dep_time=755, dep_delay=568.0),
        # Spring forward: 20:20 EST -> 05:09 EDT is 469 elapsed minutes, but 529 was reported. Invalid.
        flight(flight=3, year=2023, month=3, day=11, sched_dep_time=2020, dep_time=509, dep_delay=529.0, carrier="B6"),
    ])
    run(replace(config, max_reject_rate=1.0))
    assert query("SELECT source_row, check_name FROM quarantine.flights") == [(3, "dep_delay_consistent")]


# ---- warnings keep the row --------------------------------------------------------------

def test_warn_checks_flag_but_keep_rows(config, query):
    write_landing(config.data_dir, GOOD + [flight(flight=400, tailnum="N999")])  # not in the registry
    summary = run(config)

    assert summary.fact_rows == 4
    assert dq(query, "aircraft_in_registry") == 1
    assert query("SELECT aircraft_in_registry FROM warehouse.fact_flights WHERE tailnum = 'N999'") == [(False,)]


def test_missing_weather_hours_are_counted(config, query):
    write_landing(config.data_dir, GOOD)  # weather only for the flights' own hours
    run(config)
    # Two flight dates x 24 hours x 2 origins with flights, minus the 3 hours that have readings
    assert dq(query, "weather_hours_complete") == 2 * 24 * 2 - 3
    assert dq(query, "weather_for_flight_hour") == 0


def test_volume_check_warns_on_a_big_drop(config, query):
    write_landing(config.data_dir, [flight(flight=n) for n in range(1, 21)])
    run(config)
    write_landing(config.data_dir, [flight(flight=n) for n in range(1, 11)])
    summary = run(config)

    assert summary.status == "succeeded"
    assert dq(query, "volume_vs_last_run") == 1


# ---- failures never damage the published warehouse --------------------------------------

def test_circuit_breaker_keeps_the_previous_warehouse(config, query):
    write_landing(config.data_dir, GOOD)
    run(config)

    write_landing(config.data_dir, GOOD + [flight(flight=900 + n, carrier="ZZ") for n in range(5)])
    summary = run(config)  # 5 of 8 rows bad, far above the 1% limit

    assert summary.status == "failed"
    assert "DataQualityError" in summary.error
    assert query("SELECT count(*) FROM warehouse.fact_flights") == [(3,)]
    assert query("SELECT status FROM meta.pipeline_runs ORDER BY started_at") == [("succeeded",), ("failed",)]
    assert query("SELECT count(*) FROM quarantine.flights") == [(5,)]  # kept for debugging


def test_failed_reconciliation_rolls_back_the_transform(config, query, monkeypatch):
    write_landing(config.data_dir, GOOD)
    run(config)

    write_landing(config.data_dir, GOOD + [flight(flight=500)])
    monkeypatch.setattr(
        transform_module, "reconcile",
        lambda con: [CheckResult("reconcile_row_counts", "error", "forced mismatch", 1)],
    )
    summary = run(config)

    assert summary.status == "failed"
    assert "ReconciliationError" in summary.error
    assert query("SELECT count(*) FROM warehouse.fact_flights") == [(3,)]  # still the old version
