"""
Decision Journal -- the Phase 2 daily-use log.

SEQUENCING DECISION (per handoff doc Step 6, resolved now rather than left
ambiguous):

    The "Outcome" field is filled in as a PHASE 3 BATCH REVIEW, not logged
    daily during Phase 2.

    Rationale: an "outcome" for a same-day decision usually isn't knowable
    same-day -- it requires forward price action that the historical
    storage layer (storage.py) is already capturing automatically every
    day via the Price column. Asking for a same-day Outcome entry would
    either be premature guessing or would turn into an ongoing
    daily chore that outlives the two-week window, which is exactly the
    kind of scope creep the handoff doc flags. Instead:

        - During Phase 2: log Date, Decision, Dashboard-influenced (Y/N),
          Reason, and Notes only. Leave Outcome blank.
        - At the start of Phase 3: run `batch_review()` once, which joins
          each journal entry's Ticker (if mentioned) against the
          historical store's forward price action and proposes an
          Outcome value for human confirmation/edit.

This keeps Phase 2 to the lightest possible daily touch (a few lines,
once a day) while still producing a real Outcome field by Phase 3 --
at no cost, since the raw data (Price history) needed to compute it is
already being captured from Day 1 regardless.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import storage

JOURNAL_PATH = storage.DATA_DIR / "decision_journal.csv"

JOURNAL_COLUMNS = ["Date", "Decision", "DashboardInfluenced", "Reason", "Outcome", "Notes", "Ticker"]


def log_decision(date, decision: str, dashboard_influenced: bool, reason: str,
                  notes: str = "", ticker: str | None = None) -> None:
    """
    Log one daily entry. Outcome is intentionally left blank here -- see
    module docstring for why that's filled in later, in Phase 3, not now.
    """
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{
        "Date": pd.to_datetime(date).normalize(),
        "Decision": decision,
        "DashboardInfluenced": "Y" if dashboard_influenced else "N",
        "Reason": reason,
        "Outcome": "",  # deliberately blank -- see SEQUENCING DECISION above
        "Notes": notes,
        "Ticker": ticker or "",
    }])
    write_header = not JOURNAL_PATH.exists()
    row.to_csv(JOURNAL_PATH, mode="a", header=write_header, index=False)


def read_journal() -> pd.DataFrame:
    if not JOURNAL_PATH.exists():
        return pd.DataFrame(columns=JOURNAL_COLUMNS)
    return pd.read_csv(JOURNAL_PATH, parse_dates=["Date"])


def batch_review(forward_window_days: int = 21) -> pd.DataFrame:
    """
    Phase 3 entry point: for every journal row that names a Ticker, look
    up that ticker's price on the decision date and `forward_window_days`
    of trading days later (from the historical store) and propose a
    forward-return-based Outcome string for human review/override.

    Does not write anything back automatically -- prints proposed outcomes
    for a human to confirm, consistent with "evidence-based, not
    automatic" improvements in Phase 3.
    """
    journal = read_journal()
    if journal.empty:
        return journal

    hist = storage.read_history(columns=["Date", "Ticker", "Price"])
    proposals = []
    for _, row in journal.iterrows():
        if not row["Ticker"]:
            proposals.append(None)
            continue
        tick_hist = hist[hist["Ticker"] == row["Ticker"]].sort_values("Date")
        on_or_after = tick_hist[tick_hist["Date"] >= row["Date"]]
        if on_or_after.empty:
            proposals.append(None)
            continue
        start_idx = on_or_after.index[0]
        start_price = tick_hist.loc[start_idx, "Price"]
        future = tick_hist[tick_hist.index > start_idx]
        if len(future) < forward_window_days:
            proposals.append(None)  # not enough forward history yet
            continue
        end_price = future.iloc[forward_window_days - 1]["Price"]
        fwd_ret = end_price / start_price - 1.0
        proposals.append(f"{fwd_ret:+.1%} over {forward_window_days}d")

    journal["ProposedOutcome"] = proposals
    return journal


if __name__ == "__main__":
    print(f"Journal stored at: {JOURNAL_PATH}")
    print(read_journal())
