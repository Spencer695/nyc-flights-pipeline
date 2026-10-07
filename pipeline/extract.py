"""Extract: download each source file, then land it as Parquet.

Two layers are kept on disk on purpose:
  data/raw/      the bytes exactly as downloaded (with a SHA-256 in the manifest)
  data/landing/  the same data as typed Parquet, plus a `source_row` column so any
                 warehouse or quarantine row can be traced back to its source record
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyreadr

from .config import Config
from .telemetry import log

# R stores integer columns that contain NA as doubles, so these arrive as a mix of
# ints and floats. Cast them to nullable integers so the warehouse types are honest.
NULLABLE_INT_COLUMNS = {
    "flights": ["dep_time", "arr_time"],
    "planes": ["year"],
}


def download(url: str, dest: Path, retries: int = 3, backoff_s: float = 2.0) -> None:
    """Download to a temp file and rename, so a failed download never leaves a half file."""
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            tmp.replace(dest)
            return
        except OSError as exc:  # URLError and timeouts are OSErrors
            log("download_retry", url=url, attempt=attempt, error=str(exc))
            if attempt == retries:
                raise
            time.sleep(backoff_s**attempt)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rda(path: Path) -> pd.DataFrame:
    result = pyreadr.read_r(str(path))
    return next(iter(result.values()))


def normalize(table: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NULLABLE_INT_COLUMNS.get(table, []):
        df[col] = pd.to_numeric(df[col]).astype("Int32")
    # Position in the source file: stable across runs, so it doubles as lineage.
    df.insert(0, "source_row", range(1, len(df) + 1))
    return df


def extract(config: Config, refresh: bool = False) -> dict[str, int]:
    """Download (unless cached) and land every table. Returns rows landed per table."""
    config.ensure_dirs()
    manifest: dict[str, dict] = {}
    rows: dict[str, int] = {}
    for table in config.tables:
        url = f"{config.source_base_url}/{table}.rda"
        raw_path = config.raw_dir / f"{table}.rda"
        if refresh or not raw_path.exists():
            download(url, raw_path)
        df = normalize(table, read_rda(raw_path))
        df.to_parquet(config.landing_dir / f"{table}.parquet", index=False)
        rows[table] = len(df)
        manifest[table] = {
            "source_url": url,
            "sha256": sha256(raw_path),
            "bytes": raw_path.stat().st_size,
            "rows": len(df),
            "landed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        log("landed", table=table, rows=len(df))
    (config.landing_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return rows
