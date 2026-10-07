"""Pipeline settings. Everything path-related hangs off one data directory so
tests can point a whole run at a temporary folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Source: the nycflights23 R package (every flight that left JFK, LGA, or EWR in 2023,
# plus the airline, airport, aircraft, and hourly weather tables that describe them).
SOURCE_BASE_URL = "https://raw.githubusercontent.com/moderndive/nycflights23/main/data"

# Load order matters only for readability; every table is landed and loaded the same way.
TABLES = ("airlines", "airports", "planes", "weather", "flights")


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    source_base_url: str = SOURCE_BASE_URL
    # Circuit breaker: if more than this share of flight rows fail an error-level
    # check, the run stops before the warehouse is touched.
    max_reject_rate: float = 0.01
    # Volume check: warn when today's flight count moves more than this vs. the last good run.
    max_volume_change: float = 0.10
    tables: tuple[str, ...] = field(default=TABLES)

    @property
    def raw_dir(self) -> Path:
        """Files exactly as downloaded from the source."""
        return self.data_dir / "raw"

    @property
    def landing_dir(self) -> Path:
        """Raw files converted to Parquet, one file per source table."""
        return self.data_dir / "landing"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flights.duckdb"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.landing_dir):
            d.mkdir(parents=True, exist_ok=True)
