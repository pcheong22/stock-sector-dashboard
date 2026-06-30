"""
Daily pipeline: fetch -> features -> score -> store.

This is the script meant to run once per trading day (cron / Task
Scheduler / GitHub Action -- whatever fits). It is intentionally a thin
script with no logic of its own: each step delegates to its own module so
any piece (data source, feature set, model) can be swapped independently.

Usage:
    python run_daily.py
    python run_daily.py --overwrite   # redo today's run deliberately
"""

from __future__ import annotations

import argparse
import logging

from data_fetch import fetch_price_history
from features import build_features
from momentum_model import MomentumModel
import storage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("run_daily")


def run(overwrite: bool = False) -> None:
    log.info("Step 1/4: fetching price history...")
    prices = fetch_price_history()

    log.info("Step 2/4: building features...")
    feats = build_features(prices)
    log.info("Built features for %d tickers as of %s", len(feats), feats["Date"].max())

    log.info("Step 3/4: scoring with MomentumModel...")
    model = MomentumModel()
    scored = model.score(feats)

    log.info("Step 4/4: storing results...")
    run_date = scored["Date"].iloc[0]
    if overwrite:
        n = storage.overwrite_date(scored, run_date)
        log.info("Done. Stored %d rows for %s.", n, run_date.date())
    else:
        try:
            n = storage.append_daily_run(scored)
            log.info("Done. Stored %d rows for %s.", n, run_date.date())
        except ValueError as e:
            # Already have data for this date (e.g. a same-day manual re-run,
            # or the scheduled job firing twice). Not an error -- just a no-op.
            log.info("Nothing new to store: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true", help="Replace today's run if it already exists.")
    args = parser.parse_args()
    run(overwrite=args.overwrite)
