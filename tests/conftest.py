"""Test fixtures: tiny hand-built source tables written straight to landing/,
so the suite runs offline in a couple of seconds and every row is known."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pipeline.config import Config
from pipeline.extract import normalize

AIRLINES = [("AA", "American Airlines Inc."), ("B6", "JetBlue Airways"), ("DL", "Delta Air Lines Inc.")]
AIRPORTS = [
    ("JFK", "John F Kennedy Intl", 40.64, -73.78, 13.0, -5.0, "A", "America/New_York"),
    ("LGA", "La Guardia", 40.78, -73.87, 22.0, -5.0, "A", "America/New_York"),
    ("LAX", "Los Angeles Intl", 33.94, -118.41, 125.0, -8.0, "A", "America/Los_Angeles"),
    ("ROC", "Greater Rochester Intl", 43.12, -77.67, 559.0, -5.0, "A", "America/New_York"),
]
PLANES = [
    ("N1", 2015, "Fixed wing multi engine", "AIRBUS", "A321", 2, 190, 0, "Turbo-fan"),
    ("N2", 2010, "Fixed wing multi engine", "BOEING", "737-800", 2, 160, 0, "Turbo-fan"),
]


def flight(**overrides) -> dict:
    """A clean, on-time flight. Override any field to make it interesting."""
    row = dict(
        year=2023, month=7, day=1, dep_time=905, sched_dep_time=900, dep_delay=5.0,
        arr_time=1200, sched_arr_time=1155, arr_delay=5.0, carrier="AA", flight=100,
        tailnum="N1", origin="JFK", dest="LAX", air_time=330.0, distance=2475.0,
    )
    row.update(overrides)
    row.setdefault("hour", float(row["sched_dep_time"] // 100))
    row.setdefault("minute", float(row["sched_dep_time"] % 100))
    if "time_hour" not in row:
        try:
            row["time_hour"] = pd.Timestamp(row["year"], row["month"], row["day"], int(row["hour"]))
        except ValueError:  # impossible date on purpose
            row["time_hour"] = pd.Timestamp(2023, 1, 1)
    return row


def weather_for(flights: list[dict]) -> pd.DataFrame:
    hours = {(f["origin"], f["time_hour"]) for f in flights if f.get("origin")}
    rows = [
        dict(origin=o, year=t.year, month=t.month, day=t.day, hour=t.hour, temp=70.0, dewp=50.0, humid=50.0,
             wind_dir=180.0, wind_speed=8.0, wind_gust=9.0, precip=0.0, pressure=1015.0, visib=10.0, time_hour=t)
        for o, t in sorted(hours)
    ]
    return pd.DataFrame(rows)


def write_landing(data_dir: Path, flights: list[dict], weather: pd.DataFrame | None = None) -> None:
    tables = {
        "airlines": pd.DataFrame(AIRLINES, columns=["carrier", "name"]),
        "airports": pd.DataFrame(AIRPORTS, columns=["faa", "name", "lat", "lon", "alt", "tz", "dst", "tzone"]),
        "planes": pd.DataFrame(PLANES, columns=["tailnum", "year", "type", "manufacturer", "model",
                                                "engines", "seats", "speed", "engine"]),
        "weather": weather if weather is not None else weather_for(flights),
        "flights": pd.DataFrame(flights),
    }
    landing = data_dir / "landing"
    landing.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        normalize(name, df).to_parquet(landing / f"{name}.parquet", index=False)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path)


@pytest.fixture
def query(config: Config):
    def _query(sql: str, params=None):
        con = duckdb.connect(str(config.db_path), read_only=True)
        try:
            return con.execute(sql, params or []).fetchall()
        finally:
            con.close()
    return _query
