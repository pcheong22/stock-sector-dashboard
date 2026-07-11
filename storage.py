"""
Append-only historical storage layer.

This database -- not the model, not the dashboard UI -- is the project's
most valuable long-term asset (per the handoff doc). Every daily run adds
new rows; nothing is ever overwritten or deleted by normal operation.

Schema (per ticker per day), matching the handoff doc exactly:
    Date, Ticker, Sector, GlobalScore, SectorScore, Price,
    <engineered feature columns...>, ModelVersion, CorporateActionNote

Stored as a single partitioned Parquet dataset (partitioned by Date) for
efficient append + later analytical queries. A flat CSV mirror of the
full history is also maintained for easy manual inspection -- drop the
CSV mirror later if the dataset gets large; the Parquet store is the
source of truth.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from features import FEATURE_COLUMNS

log = logging.getLogger("storage")

# Override via the STOCK_DASH_DATA_DIR env var to point this at, e.g., a
# mounted Google Drive folder in Colab so history survives across sessions.
DATA_DIR = Path(os.environ.get("STOCK_DASH_DATA_DIR", Path(__file__).parent / "data"))
PARQUET_DIR = DATA_DIR / "history_parquet"
CSV_MIRROR = DATA_DIR / "history.csv"

SCHEMA_COLUMNS = (
    ["Date", "Ticker", "Sector", "GlobalScore", "SectorScore", "Price"]
    + FEATURE_COLUMNS
    + ["ModelVersion", "CorporateActionNote"]
)


def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "CorporateActionNote" not in df.columns:
        df["CorporateActionNote"] = pd.NA  # nullable text field, per handoff doc
    missing = [c for c in SCHEMA_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Cannot store rows missing required schema columns: {missing}")
    return df[SCHEMA_COLUMNS]


def append_daily_run(scored: pd.DataFrame) -> int:
    """
    Append one day's scored output to the historical store.

    Refuses to silently double-write: if rows for this Date already exist,
    raises -- re-running a day's pipeline should be a deliberate, explicit
    overwrite (use `overwrite_date` below), not an accidental append that
    duplicates history.

    Returns the number of rows written.
    """
    df = _ensure_schema(scored)
    df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing_dates = set()
    if PARQUET_DIR.exists():
        existing = read_history(columns=["Date"])
        existing_dates = set(existing["Date"].unique()) if not existing.empty else set()

    incoming_dates = set(df["Date"].unique())
    clash = incoming_dates & existing_dates
    if clash:
        raise ValueError(
            f"Refusing to append: data already exists for {sorted(str(d.date()) for d in clash)}. "
            f"Use overwrite_date() if you intentionally want to redo a day."
        )

    df.to_parquet(PARQUET_DIR, partition_cols=["Date"], index=False)
    _append_csv_mirror(df)
    log.info("Appended %d rows for date(s): %s", len(df), sorted(str(d.date()) for d in incoming_dates))
    return len(df)


def overwrite_date(scored: pd.DataFrame, date) -> int:
    """Deliberately replace an existing day's rows (e.g. correcting a bad run)."""
    date = pd.to_datetime(date).normalize()
    df = _ensure_schema(scored)
    df["Date"] = date

    if PARQUET_DIR.exists():
        existing = read_history()
        existing = existing[existing["Date"] != date]
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Full rewrite of the partitioned dataset -- fine at Phase-1 data volumes.
    import shutil
    if PARQUET_DIR.exists():
        shutil.rmtree(PARQUET_DIR)
    combined.to_parquet(PARQUET_DIR, partition_cols=["Date"], index=False)
    combined.sort_values(["Date", "Ticker"]).to_csv(CSV_MIRROR, index=False)
    log.info("Overwrote %d rows for date %s", len(df), date.date())
    return len(df)


def _append_csv_mirror(df: pd.DataFrame) -> None:
    write_header = not CSV_MIRROR.exists()
    df.to_csv(CSV_MIRROR, mode="a", header=write_header, index=False)


def read_history(columns: list[str] | None = None, tickers: list[str] | None = None,
                  start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Read back the historical store, optionally filtered."""
    if not PARQUET_DIR.exists():
        return pd.DataFrame(columns=columns or SCHEMA_COLUMNS)

    filters = []
    if tickers:
        filters.append(("Ticker", "in", tickers))
    if start:
        filters.append(("Date", ">=", pd.to_datetime(start)))
    if end:
        filters.append(("Date", "<=", pd.to_datetime(end)))

    df = pd.read_parquet(PARQUET_DIR, columns=columns, filters=filters or None)
    sort_cols = [c for c in ["Date", "Ticker"] if c in df.columns]
    return df.sort_values(sort_cols).reset_index(drop=True)


def latest_date() -> pd.Timestamp | None:
    hist = read_history(columns=["Date"])
    return hist["Date"].max() if not hist.empty else None


def flag_corporate_action(date, ticker: str, note: str) -> None:
    """
    Inline-flag a structural event (spin-off, reverse split, etc.) for an
    existing stored row -- exists specifically so this doesn't require
    after-the-fact detective work months later, per the HON lesson from
    the AION project.
    """
    date = pd.to_datetime(date).normalize()
    hist = read_history()
    mask = (hist["Date"] == date) & (hist["Ticker"] == ticker)
    if not mask.any():
        raise ValueError(f"No stored row found for {ticker} on {date.date()} to flag.")
    hist.loc[mask, "CorporateActionNote"] = note
    overwrite_date(hist[hist["Date"] == date], date)


if __name__ == "__main__":
    print("Schema columns:", SCHEMA_COLUMNS)
    print("Current latest stored date:", latest_date())
