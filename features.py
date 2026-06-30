"""
Feature engineering pipeline.

Reuses the AION-project feature set validated during the reverse-engineering
phase:
    - 1M / 3M / 6M / 12M returns
    - Distance from 52-week high / low
    - Realized volatility (annualized, trailing 21d)
    - 63-day trend R^2 (goodness-of-fit of a linear trend through log price)
    - RSI-14
    - Relative return vs SPY (market) and vs the ticker's sector ETF

Input: long-format price history (Date, Ticker, Open, High, Low, Close, Volume)
       as returned by data_fetch.fetch_price_history().
Output: one row per (Date, Ticker) for the *most recent* date available,
        with all engineered features as columns -- this is what gets fed
        into ScoringEngine implementations and stored alongside the score.

Trading-day window conventions (approximate calendar->trading-day mapping):
    1M ~ 21, 3M ~ 63, 6M ~ 126, 12M ~ 252, 52-week ~ 252
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import MARKET_BENCHMARK, SECTOR_ETFS, ticker_sector_map

TRADING_DAYS = {"1M": 21, "3M": 63, "6M": 126, "12M": 252, "52W": 252}
TREND_WINDOW = 63
VOL_WINDOW = 21
RSI_WINDOW = 14


def _pct_return(close: pd.Series, window: int) -> float:
    if len(close) < window + 1:
        return np.nan
    past, current = close.iloc[-window - 1], close.iloc[-1]
    if past == 0 or pd.isna(past):
        return np.nan
    return current / past - 1.0


def _dist_from_high_low(close: pd.Series, window: int) -> tuple[float, float]:
    if len(close) < window:
        window_slice = close
    else:
        window_slice = close.iloc[-window:]
    hi, lo, last = window_slice.max(), window_slice.min(), close.iloc[-1]
    dist_from_high = last / hi - 1.0 if hi else np.nan
    dist_from_low = last / lo - 1.0 if lo else np.nan
    return dist_from_high, dist_from_low


def _realized_vol(close: pd.Series, window: int = VOL_WINDOW) -> float:
    rets = close.pct_change().dropna()
    if len(rets) < window:
        return np.nan
    return rets.iloc[-window:].std() * np.sqrt(252)


def _trend_r2(close: pd.Series, window: int = TREND_WINDOW) -> float:
    if len(close) < window:
        return np.nan
    y = np.log(close.iloc[-window:].values)
    x = np.arange(window)
    if np.any(~np.isfinite(y)):
        return np.nan
    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    if ss_tot == 0:
        return np.nan
    r2 = 1 - ss_res / ss_tot
    # sign-carrying: a tight downtrend should not look identical to a tight uptrend
    return r2 if slope >= 0 else -r2


def _rsi(close: pd.Series, window: int = RSI_WINDOW) -> float:
    if len(close) < window + 1:
        return np.nan
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.iloc[-window:].mean()
    avg_loss = loss.iloc[-window:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _features_for_series(close: pd.Series) -> dict:
    feats = {}
    for label, window in (("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)):
        feats[f"ret_{label}"] = _pct_return(close, window)
    dist_high, dist_low = _dist_from_high_low(close, TRADING_DAYS["52W"])
    feats["dist_from_52w_high"] = dist_high
    feats["dist_from_52w_low"] = dist_low
    feats["realized_vol_21d"] = _realized_vol(close)
    feats["trend_r2_63d"] = _trend_r2(close)
    feats["rsi_14"] = _rsi(close)
    return feats


def build_features(price_history: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the full feature set for every ticker, as of the most recent
    date present in price_history. Returns one row per ticker.
    """
    sector_map = ticker_sector_map()
    latest_date = price_history["Date"].max()

    # Pre-compute benchmark / sector ETF return series (same windows) so each
    # stock's relative-return features are simple subtractions.
    bench_close = price_history.loc[
        price_history["Ticker"] == MARKET_BENCHMARK
    ].sort_values("Date")["Close"]
    bench_feats = _features_for_series(bench_close) if len(bench_close) else {}

    sector_etf_feats = {}
    for sector, etf in SECTOR_ETFS.items():
        etf_close = price_history.loc[price_history["Ticker"] == etf].sort_values("Date")["Close"]
        if len(etf_close):
            sector_etf_feats[sector] = _features_for_series(etf_close)

    rows = []
    for ticker, g in price_history.groupby("Ticker"):
        if ticker not in sector_map:
            continue  # benchmark / sector ETFs are inputs, not scored rows
        g = g.sort_values("Date")
        close = g["Close"]
        if close.empty:
            continue

        feats = _features_for_series(close)
        sector = sector_map[ticker]

        # Relative returns vs market and vs sector ETF, using 3M as the
        # representative horizon (matches the AION-project methodology).
        feats["rel_ret_3M_vs_market"] = feats.get("ret_3M", np.nan) - bench_feats.get("ret_3M", np.nan)
        sect_f = sector_etf_feats.get(sector, {})
        feats["rel_ret_3M_vs_sector"] = feats.get("ret_3M", np.nan) - sect_f.get("ret_3M", np.nan)

        rows.append({
            "Date": latest_date,
            "Ticker": ticker,
            "Sector": sector,
            "Price": close.iloc[-1],
            **feats,
        })

    return pd.DataFrame(rows)


FEATURE_COLUMNS = [
    "ret_1M", "ret_3M", "ret_6M", "ret_12M",
    "dist_from_52w_high", "dist_from_52w_low",
    "realized_vol_21d", "trend_r2_63d", "rsi_14",
    "rel_ret_3M_vs_market", "rel_ret_3M_vs_sector",
]


if __name__ == "__main__":
    # Quick smoke test with synthetic data (no network needed).
    rng = pd.date_range("2025-01-01", periods=300, freq="B")
    np.random.seed(0)
    frames = []
    for sym in ["AAPL", "SPY", "XLK"]:
        price = 100 * np.exp(np.cumsum(np.random.normal(0.0003, 0.01, len(rng))))
        frames.append(pd.DataFrame({
            "Date": rng, "Ticker": sym, "Open": price, "High": price * 1.01,
            "Low": price * 0.99, "Close": price, "Volume": 1_000_000,
        }))
    synthetic = pd.concat(frames, ignore_index=True)

    import config
    config.SECTOR_TICKERS = {"Technology": ["AAPL"]}
    feats = build_features(synthetic)
    print(feats[["Ticker", "Sector"] + FEATURE_COLUMNS])
