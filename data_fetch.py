"""
Daily data pull (free price data via Yahoo Finance / yfinance).

Pulls enough trailing history (default ~14 months) to compute all the
features in features.py (12M returns, 52-week hi/lo, 63-day trend, etc.)
for every ticker in the universe, plus the market benchmark and sector
ETFs needed for relative-return features.

This is a starting point reusing the AION-project price-fetching
approach -- swap the implementation later (different vendor, intraday,
adjusted-close handling, etc.) without touching anything downstream,
since everything downstream only consumes the long-format DataFrame
returned by `fetch_price_history`.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

from config import all_fetch_symbols

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("data_fetch")

LOOKBACK_PERIOD = "14mo"  # comfortably covers a trailing 252-trading-day window


def fetch_price_history(symbols: list[str] | None = None, period: str = LOOKBACK_PERIOD) -> pd.DataFrame:
    """
    Pull daily OHLCV history for the given symbols (default: full universe
    + benchmark + sector ETFs).

    Returns a long-format DataFrame:
        columns = [Date, Ticker, Open, High, Low, Close, Volume]
    sorted by Ticker, Date. Close is the adjusted close (auto_adjust=True),
    which is what we want for return calculations across splits/dividends.
    """
    symbols = symbols or all_fetch_symbols()
    log.info("Fetching %d symbols over period=%s", len(symbols), period)

    frames = []
    failed = []
    # yfinance can batch-download, but per-symbol calls are more robust to
    # one bad ticker poisoning the whole batch -- acceptable tradeoff for a
    # universe this size run once a day.
    for sym in symbols:
        for attempt in range(3):
            try:
                df = yf.Ticker(sym).history(period=period, auto_adjust=True)
                if df is None or df.empty:
                    raise ValueError("empty history")
                df = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
                df["Ticker"] = sym
                # yfinance returns tz-aware timestamps; normalize to plain date
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
                frames.append(df)
                break
            except Exception as e:
                if attempt == 2:
                    log.warning("Failed to fetch %s after 3 attempts: %s", sym, e)
                    failed.append(sym)
                else:
                    time.sleep(1)

    if failed:
        log.warning("Symbols with no data this run: %s", failed)

    if not frames:
        raise RuntimeError("No price data fetched for any symbol -- check network/connectivity.")

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["Ticker", "Date"]).reset_index(drop=True)
    return out


if __name__ == "__main__":
    df = fetch_price_history()
    print(df.tail())
    print(f"\nFetched {df['Ticker'].nunique()} tickers, {df['Date'].max()} latest date.")
