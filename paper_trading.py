"""Prospective, non-live paper portfolio for the sector dashboard.

This module deliberately has no broker integration. It records hypothetical
signals, next-close executions, costs, and daily marked-to-market equity for:

* an equal-weight Top-5 sector ETF portfolio;
* a fixed-notional Top-5 minus Bottom-5 portfolio; and
* a buy-and-hold SPY comparator.

The rules are frozen at a 10-trading-day signal cadence and 10 bps one-way
costs. State is append-only CSV plus a small JSON checkpoint in ``data/``.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import MARKET_BENCHMARK, SECTOR_ETFS
from momentum_model import aggregate_sector_breadth

HORIZON_TRADING_DAYS = 10
TOP_N = 5
COST_BPS = 10
COST_RATE = COST_BPS / 10_000
STATE_VERSION = 1

STATE_FILE = "paper_trading_state.json"
SIGNALS_FILE = "paper_trading_signals.csv"
ORDERS_FILE = "paper_trading_orders.csv"
LEDGER_FILE = "paper_trading_ledger.csv"

SIGNAL_COLUMNS = [
    "SignalDate", "EntryRule", "HorizonTradingDays", "TopSectors",
    "BottomSectors", "ScoreMetric", "ModelVersion", "Status", "ExecutedDate",
]
ORDER_COLUMNS = [
    "ExecutionDate", "SignalDate", "HorizonTradingDays", "CostBps",
    "TopTurnover", "BottomTurnover", "TopSectors", "BottomSectors",
    "Top5Cost", "LongShortCost", "LiveOrder",
]
LEDGER_COLUMNS = [
    "Date", "Top5Equity", "LongShortEquity", "SPYEquity",
    "Top5DailyReturn", "LongShortDailyReturn", "SPYDailyReturn",
    "TopSectors", "BottomSectors", "PendingSignalDate", "Event",
]


def _paths(data_dir: Path) -> dict[str, Path]:
    return {
        "state": data_dir / STATE_FILE,
        "signals": data_dir / SIGNALS_FILE,
        "orders": data_dir / ORDERS_FILE,
        "ledger": data_dir / LEDGER_FILE,
    }


def _default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "last_processed_date": None,
        "last_signal_date": None,
        "trading_days_since_signal": 0,
        "pending_signal": None,
        "active_top": [],
        "active_bottom": [],
        "last_prices": {},
        "top5_equity": 1.0,
        "long_short_equity": 1.0,
        "spy_equity": 1.0,
    }


def _load_state(path: Path) -> dict:
    if not path.exists():
        return _default_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION:
        raise ValueError(
            f"Unsupported paper-trading state version {state.get('version')}; "
            f"expected {STATE_VERSION}."
        )
    return state


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} is missing columns: {missing}")
    return frame[columns]


def _write_csv(path: Path, frame: pd.DataFrame, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame[columns].to_csv(temp, index=False, quoting=csv.QUOTE_MINIMAL)
    os.replace(temp, path)


def _append_unique(path: Path, columns: list[str], row: dict, keys: list[str]) -> None:
    frame = _read_csv(path, columns)
    if not frame.empty:
        duplicate = pd.Series(True, index=frame.index)
        for key in keys:
            duplicate &= frame[key].astype(str) == str(row[key])
        if duplicate.any():
            return
    new_row = pd.DataFrame([[row.get(c, "") for c in columns]], columns=columns)
    frame = new_row if frame.empty else pd.concat([frame, new_row], ignore_index=True)
    _write_csv(path, frame, columns)


def _date_string(value) -> str:
    return pd.Timestamp(value).normalize().strftime("%Y-%m-%d")


def _rank_sectors(scored: pd.DataFrame) -> tuple[list[str], list[str]]:
    ranked = aggregate_sector_breadth(scored).sort_values(
        ["AvgGlobalScore", "Sector"], ascending=[False, True]
    )
    if len(ranked) < TOP_N * 2:
        raise ValueError(f"Need at least {TOP_N * 2} sectors; received {len(ranked)}.")
    top = ranked.head(TOP_N)["Sector"].tolist()
    bottom = ranked.tail(TOP_N)["Sector"].tolist()
    if set(top) & set(bottom):
        raise ValueError("Top and bottom paper portfolios overlap.")
    return top, bottom


def _model_version(scored: pd.DataFrame) -> str:
    versions = scored.get("ModelVersion", pd.Series(["unknown"])).dropna().astype(str).unique()
    return versions[0] if len(versions) == 1 else "mixed"


def _signal(date: str, scored: pd.DataFrame) -> dict:
    top, bottom = _rank_sectors(scored)
    return {
        "signal_date": date,
        "top": top,
        "bottom": bottom,
        "model_version": _model_version(scored),
    }


def _signal_row(signal: dict) -> dict:
    return {
        "SignalDate": signal["signal_date"],
        "EntryRule": "Next trading-day close",
        "HorizonTradingDays": HORIZON_TRADING_DAYS,
        "TopSectors": " | ".join(signal["top"]),
        "BottomSectors": " | ".join(signal["bottom"]),
        "ScoreMetric": "Average constituent GlobalScore",
        "ModelVersion": signal["model_version"],
        "Status": "Pending",
        "ExecutedDate": "",
    }


def _mark_signal_executed(path: Path, signal_date: str, execution_date: str) -> None:
    frame = _read_csv(path, SIGNAL_COLUMNS)
    mask = frame["SignalDate"] == signal_date
    if mask.sum() != 1:
        raise ValueError(f"Expected one pending signal for {signal_date}; found {int(mask.sum())}.")
    frame.loc[mask, "Status"] = "Executed"
    frame.loc[mask, "ExecutedDate"] = execution_date
    _write_csv(path, frame, SIGNAL_COLUMNS)


def _close_map(price_history: pd.DataFrame, run_date: str) -> dict[str, float]:
    required = {"Date", "Ticker", "Close"}
    missing = sorted(required - set(price_history.columns))
    if missing:
        raise ValueError(f"Price history is missing columns: {missing}")
    prices = price_history.copy()
    prices["Date"] = pd.to_datetime(prices["Date"], format="mixed").dt.normalize()
    day = prices[prices["Date"] == pd.Timestamp(run_date)]
    closes = day.drop_duplicates("Ticker", keep="last").set_index("Ticker")["Close"].to_dict()
    return {ticker: float(value) for ticker, value in closes.items() if pd.notna(value)}


def _required_symbols(sectors: Iterable[str]) -> list[str]:
    return [SECTOR_ETFS[sector] for sector in sectors]


def _validate_closes(closes: dict[str, float], sectors: Iterable[str]) -> None:
    symbols = set(_required_symbols(sectors)) | {MARKET_BENCHMARK}
    missing = sorted(symbol for symbol in symbols if symbol not in closes or closes[symbol] <= 0)
    if missing:
        raise ValueError(f"Missing valid next-close prices for: {missing}")


def _mean_return(sectors: list[str], old: dict[str, float], new: dict[str, float]) -> float:
    returns = []
    for symbol in _required_symbols(sectors):
        if symbol not in old or old[symbol] <= 0 or symbol not in new or new[symbol] <= 0:
            raise ValueError(f"Cannot mark paper position for {symbol}: missing valid close.")
        returns.append(new[symbol] / old[symbol] - 1)
    return sum(returns) / len(returns)


def _turnover(previous: list[str], current: list[str]) -> float:
    if not previous:
        return 1.0
    overlap = len(set(previous) & set(current))
    return 1 - overlap / TOP_N


def _current_prices(closes: dict[str, float], top: list[str], bottom: list[str]) -> dict[str, float]:
    symbols = set(_required_symbols(top + bottom)) | {MARKET_BENCHMARK}
    return {symbol: closes[symbol] for symbol in sorted(symbols)}


def initialize_from_history(history: pd.DataFrame, data_dir: Path) -> dict:
    """Create the first pending signal from the latest stored dashboard date.

    No position is opened here because the frozen rule requires execution at
    the *next* trading-day close.
    """
    if history.empty:
        raise ValueError("Cannot initialize paper trading without dashboard history.")
    dates = pd.to_datetime(history["Date"], format="mixed").dt.normalize()
    latest = dates.max()
    day = history.loc[dates == latest].copy()
    return update(day, price_history=None, data_dir=data_dir, initialize_only=True)


def update(
    scored: pd.DataFrame,
    price_history: pd.DataFrame | None,
    data_dir: Path,
    initialize_only: bool = False,
) -> dict:
    """Advance the paper ledger by one newly stored dashboard date."""
    if scored.empty:
        raise ValueError("Cannot update paper trading with an empty scored frame.")
    run_dates = pd.to_datetime(scored["Date"], format="mixed").dt.normalize().unique()
    if len(run_dates) != 1:
        raise ValueError("Paper trading expects exactly one scored date per update.")
    run_date = _date_string(run_dates[0])
    data_dir = Path(data_dir)
    paths = _paths(data_dir)
    state = _load_state(paths["state"])

    if state["last_processed_date"]:
        if run_date < state["last_processed_date"]:
            raise ValueError("Paper ledger cannot process a date older than its checkpoint.")
        if run_date == state["last_processed_date"]:
            return state

    events = []
    top_daily = 0.0
    long_short_daily = 0.0
    spy_daily = 0.0
    is_first_date = state["last_processed_date"] is None
    if not is_first_date:
        state["trading_days_since_signal"] += 1

    closes = None
    if state["active_top"] or state["pending_signal"]:
        if price_history is not None:
            closes = _close_map(price_history, run_date)

    if state["active_top"]:
        if closes is None:
            raise ValueError("Price history is required to mark active paper positions.")
        _validate_closes(closes, state["active_top"] + state["active_bottom"])
        top_daily = _mean_return(state["active_top"], state["last_prices"], closes)
        bottom_daily = _mean_return(state["active_bottom"], state["last_prices"], closes)
        long_short_daily = top_daily - bottom_daily
        old_spy = state["last_prices"].get(MARKET_BENCHMARK)
        spy_daily = closes[MARKET_BENCHMARK] / old_spy - 1
        state["top5_equity"] *= 1 + top_daily
        state["long_short_equity"] *= 1 + long_short_daily
        state["spy_equity"] *= 1 + spy_daily
        events.append("Marked to market")

    pending = state["pending_signal"]
    if pending and run_date > pending["signal_date"]:
        if initialize_only or closes is None:
            raise ValueError("Next-close prices are required to execute the pending signal.")
        _validate_closes(closes, pending["top"] + pending["bottom"])
        top_turnover = _turnover(state["active_top"], pending["top"])
        bottom_turnover = _turnover(state["active_bottom"], pending["bottom"])
        top_cost = COST_RATE * top_turnover
        long_short_cost = COST_RATE * (top_turnover + bottom_turnover)
        state["top5_equity"] *= 1 - top_cost
        state["long_short_equity"] *= 1 - long_short_cost
        state["active_top"] = pending["top"]
        state["active_bottom"] = pending["bottom"]
        state["last_prices"] = _current_prices(closes, state["active_top"], state["active_bottom"])
        _append_unique(paths["orders"], ORDER_COLUMNS, {
            "ExecutionDate": run_date,
            "SignalDate": pending["signal_date"],
            "HorizonTradingDays": HORIZON_TRADING_DAYS,
            "CostBps": COST_BPS,
            "TopTurnover": top_turnover,
            "BottomTurnover": bottom_turnover,
            "TopSectors": " | ".join(pending["top"]),
            "BottomSectors": " | ".join(pending["bottom"]),
            "Top5Cost": top_cost,
            "LongShortCost": long_short_cost,
            "LiveOrder": "NO — PAPER ONLY",
        }, keys=["ExecutionDate", "SignalDate"])
        _mark_signal_executed(paths["signals"], pending["signal_date"], run_date)
        state["pending_signal"] = None
        events.append("Paper rebalance executed")

    signal_due = state["last_signal_date"] is None or (
        state["trading_days_since_signal"] >= HORIZON_TRADING_DAYS
        and state["pending_signal"] is None
    )
    if signal_due:
        new_signal = _signal(run_date, scored)
        state["pending_signal"] = new_signal
        state["last_signal_date"] = run_date
        state["trading_days_since_signal"] = 0
        _append_unique(paths["signals"], SIGNAL_COLUMNS, _signal_row(new_signal), keys=["SignalDate"])
        events.append("Signal created for next close")

    state["last_processed_date"] = run_date
    _append_unique(paths["ledger"], LEDGER_COLUMNS, {
        "Date": run_date,
        "Top5Equity": state["top5_equity"],
        "LongShortEquity": state["long_short_equity"],
        "SPYEquity": state["spy_equity"],
        "Top5DailyReturn": top_daily,
        "LongShortDailyReturn": long_short_daily,
        "SPYDailyReturn": spy_daily,
        "TopSectors": " | ".join(state["active_top"]),
        "BottomSectors": " | ".join(state["active_bottom"]),
        "PendingSignalDate": state["pending_signal"]["signal_date"] if state["pending_signal"] else "",
        "Event": "; ".join(events) if events else "No change",
    }, keys=["Date"])
    _atomic_write_json(paths["state"], state)
    return state


def dashboard_payload(data_dir: Path) -> dict:
    """Return a strict-JSON-ready summary for static and Streamlit dashboards."""
    data_dir = Path(data_dir)
    paths = _paths(data_dir)
    if not paths["state"].exists():
        return {
            "status": "Not initialized",
            "disclaimer": "Paper research only. No broker connection and no live orders.",
            "rules": f"Top/Bottom {TOP_N}; {HORIZON_TRADING_DAYS}-day signals; next-close entry; {COST_BPS} bps one-way.",
            "pending": None,
            "latest": None,
            "ledger_cols": [],
            "ledger_rows": [],
        }
    state = _load_state(paths["state"])
    ledger = _read_csv(paths["ledger"], LEDGER_COLUMNS)
    rows = []
    for row in ledger.tail(60).iloc[::-1].itertuples(index=False):
        rows.append([
            row.Date,
            float(row.Top5Equity),
            float(row.LongShortEquity),
            float(row.SPYEquity),
            row.Event,
        ])
    pending = state["pending_signal"]
    return {
        "status": "Pending first execution" if not state["active_top"] else "Active",
        "disclaimer": "Paper research only. No broker connection and no live orders.",
        "rules": f"Top/Bottom {TOP_N}; {HORIZON_TRADING_DAYS}-day signals; next-close entry; {COST_BPS} bps one-way.",
        "pending": None if pending is None else {
            "signal_date": pending["signal_date"],
            "top": pending["top"],
            "bottom": pending["bottom"],
        },
        "latest": {
            "date": state["last_processed_date"],
            "top5_equity": state["top5_equity"],
            "long_short_equity": state["long_short_equity"],
            "spy_equity": state["spy_equity"],
            "top": state["active_top"],
            "bottom": state["active_bottom"],
        },
        "ledger_cols": ["Date", "Top 5 Equity", "Long-Short Equity", "SPY Equity", "Event"],
        "ledger_rows": rows,
    }
