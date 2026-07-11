"""
Streamlit dashboard — Arrow-free rendering.
All tables rendered as HTML via st.markdown to avoid st.dataframe's
internal pyarrow dependency which segfaults on Streamlit Cloud.
"""

from __future__ import annotations
import base64, json
from datetime import date as date_cls
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

import storage
from momentum_model import aggregate_sector_breadth
from config import SECTOR_ETFS

st.set_page_config(page_title="Stock & Sector Rankings", layout="wide")

# ── helpers ──────────────────────────────────────────────────────────────────

def html_table(df: pd.DataFrame) -> None:
    st.markdown(
        df.to_html(index=True, border=0, classes="data-table"),
        unsafe_allow_html=True,
    )

def load_history() -> pd.DataFrame:
    csv = storage.CSV_MIRROR
    if not Path(csv).exists():
        return pd.DataFrame()
    return pd.read_csv(csv, parse_dates=["Date"])

# ── GitHub-backed journal ─────────────────────────────────────────────────────

JOURNAL_PATH_IN_REPO = "data/decision_journal.csv"
JOURNAL_COLUMNS = ["Date", "Decision", "DashboardInfluenced", "Reason", "Outcome", "Notes", "Ticker"]

def _gh_headers():
    return {"Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json"}

def _gh_api_url():
    return f"https://api.github.com/repos/{st.secrets['GITHUB_REPO']}/contents/{JOURNAL_PATH_IN_REPO}"

def journal_configured() -> bool:
    return "GITHUB_TOKEN" in st.secrets and "GITHUB_REPO" in st.secrets

def append_journal_entry(entry: dict) -> None:
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    resp = requests.get(_gh_api_url(), headers=_gh_headers(), params={"ref": branch})
    if resp.status_code == 200:
        payload = resp.json()
        from io import StringIO
        df = pd.read_csv(StringIO(base64.b64decode(payload["content"]).decode()))
        sha = payload["sha"]
    elif resp.status_code == 404:
        df = pd.DataFrame(columns=JOURNAL_COLUMNS)
        sha = None
    else:
        raise RuntimeError(f"Read failed: {resp.status_code} {resp.text}")
    df = pd.concat([df, pd.DataFrame([entry])], ignore_index=True)
    body = {"message": f"Journal: {entry['Date']}",
            "content": base64.b64encode(df.to_csv(index=False).encode()).decode(),
            "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(_gh_api_url(), headers=_gh_headers(), data=json.dumps(body))
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Write failed: {r.status_code} {r.text}")

def read_journal() -> pd.DataFrame:
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    resp = requests.get(_gh_api_url(), headers=_gh_headers(), params={"ref": branch})
    if resp.status_code == 404:
        return pd.DataFrame(columns=JOURNAL_COLUMNS)
    if resp.status_code != 200:
        raise RuntimeError(f"Read failed: {resp.status_code}")
    from io import StringIO
    return pd.read_csv(StringIO(base64.b64decode(resp.json()["content"]).decode()))

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    st.title("Daily Stock & Sector Ranking Dashboard")

    hist = load_history()
    if hist.empty:
        st.warning("No data yet. Run `python run_daily.py` first.")
        return

    available_dates = sorted(hist["Date"].unique(), reverse=True)
    selected_date = st.sidebar.selectbox(
        "Date", available_dates,
        format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"),
    )
    day = hist[hist["Date"] == selected_date].copy()
    st.sidebar.caption(f"Model: {day['ModelVersion'].iloc[0]}")
    st.sidebar.caption(f"{len(day)} tickers scored")

    tab_sector, tab_global, tab_drill, tab_journal = st.tabs(
        ["Sector Rankings", "Global Rankings", "Sector Drill-Down", "Log a Decision"]
    )

    # ── Sector Rankings ───────────────────────────────────────────────────────
    with tab_sector:
        st.subheader("Sector Rankings")
        sector_table = aggregate_sector_breadth(day)

        idx = available_dates.index(selected_date)
        prior_date = available_dates[idx + 1] if idx + 1 < len(available_dates) else None

        if prior_date is not None:
            prior_day = hist[hist["Date"] == prior_date]
            prior = aggregate_sector_breadth(prior_day)[["Sector", "AvgGlobalScore", "BreadthPct"]]
            prior = prior.rename(columns={"AvgGlobalScore": "_PA", "BreadthPct": "_PB"})
            sector_table = sector_table.merge(prior, on="Sector", how="left")
            sector_table["ScoreChange"] = sector_table["AvgGlobalScore"] - sector_table["_PA"]
            sector_table["BreadthChange"] = sector_table["BreadthPct"] - sector_table["_PB"]
            sector_table = sector_table.drop(columns=["_PA", "_PB"])
            st.caption(f"Changes vs. {pd.Timestamp(prior_date).strftime('%Y-%m-%d')}")

        sector_table.index = range(1, len(sector_table) + 1)
        display = sector_table.copy()
        display["AvgGlobalScore"] = display["AvgGlobalScore"].map("{:.1f}".format)
        display["MedianGlobalScore"] = display["MedianGlobalScore"].map("{:.1f}".format)
        display["BreadthPct"] = display["BreadthPct"].map("{:.0f}%".format)
        if "ScoreChange" in display.columns:
            display["ScoreChange"] = display["ScoreChange"].map(
                lambda v: f"{'▲' if v>0 else '▼' if v<0 else '→'} {v:+.1f}" if pd.notna(v) else "—")
            display["BreadthChange"] = display["BreadthChange"].map(
                lambda v: f"{'▲' if v>0 else '▼' if v<0 else '→'} {v:+.0f}pp" if pd.notna(v) else "—")
        html_table(display)

        st.bar_chart(sector_table.set_index("Sector")["AvgGlobalScore"].astype(float))

    # ── Global Rankings ───────────────────────────────────────────────────────
    with tab_global:
        st.subheader("Global Stock Rankings")
        cols = ["Ticker", "Sector", "GlobalScore", "SectorScore", "Price",
                "ret_1M", "ret_3M", "ret_6M", "ret_12M", "rsi_14"]
        ranked = day.sort_values("GlobalScore", ascending=False)[cols].reset_index(drop=True)
        ranked.index = range(1, len(ranked) + 1)
        ranked["Price"] = ranked["Price"].map("${:.2f}".format)
        for c in ["ret_1M", "ret_3M", "ret_6M", "ret_12M"]:
            ranked[c] = ranked[c].map("{:.1%}".format)
        ranked["GlobalScore"] = ranked["GlobalScore"].map("{:.1f}".format)
        ranked["SectorScore"] = ranked["SectorScore"].map("{:.1f}".format)
        ranked["rsi_14"] = ranked["rsi_14"].map("{:.0f}".format)
        html_table(ranked)

    # ── Sector Drill-Down ─────────────────────────────────────────────────────
    with tab_drill:
        st.subheader("Sector Drill-Down")
        chosen = st.selectbox("Sector", sorted(day["Sector"].unique()))
        sub = day[day["Sector"] == chosen].sort_values("SectorScore", ascending=False)
        cols = ["Ticker", "GlobalScore", "SectorScore", "Price",
                "ret_1M", "ret_3M", "ret_6M", "ret_12M",
                "dist_from_52w_high", "dist_from_52w_low", "realized_vol_21d",
                "trend_r2_63d", "rsi_14", "rel_ret_3M_vs_market", "rel_ret_3M_vs_sector"]
        sub = sub[cols].reset_index(drop=True)
        sub.index = range(1, len(sub) + 1)
        sub["Price"] = sub["Price"].map("${:.2f}".format)
        for c in ["ret_1M", "ret_3M", "ret_6M", "ret_12M",
                  "dist_from_52w_high", "dist_from_52w_low",
                  "realized_vol_21d", "rel_ret_3M_vs_market", "rel_ret_3M_vs_sector"]:
            sub[c] = sub[c].map("{:.1%}".format)
        for c in ["GlobalScore", "SectorScore"]:
            sub[c] = sub[c].map("{:.1f}".format)
        sub["trend_r2_63d"] = sub["trend_r2_63d"].map("{:.2f}".format)
        sub["rsi_14"] = sub["rsi_14"].map("{:.0f}".format)
        html_table(sub)

    # ── Log a Decision ────────────────────────────────────────────────────────
    with tab_journal:
        st.subheader("Log a Decision")
        st.caption(
            "Phase 2 daily log for sector-level relative-value calls. "
            "Outcome is filled in during Phase 3 batch review, not logged daily."
        )
        if not journal_configured():
            st.error("Add GITHUB_TOKEN and GITHUB_REPO in Settings → Secrets, then reload.")
        else:
            with st.form("journal_form", clear_on_submit=True):
                entry_date = st.date_input("Date", value=date_cls.today())
                decision_type = st.selectbox("Decision type", [
                    "Open / increase overweight",
                    "Trim / reduce overweight",
                    "Rotate into this sector from another",
                    "Watch only (no action)",
                ])
                sector = st.selectbox("Sector", sorted(SECTOR_ETFS.keys()))
                decision = st.text_area("What did you decide?", height=80)
                influenced = st.radio(
                    "Did the sector rankings change this decision vs. what you'd have done otherwise?",
                    ["Y", "N"], horizontal=True,
                )
                reason = st.text_area("Why? (cite the specific number)", height=80)
                notes = st.text_area("Anything else? (optional)", height=80)
                submitted = st.form_submit_button("Save entry")

            if submitted:
                if not decision.strip():
                    st.warning("Add a description before saving.")
                else:
                    try:
                        append_journal_entry({
                            "Date": str(entry_date),
                            "Decision": f"[{decision_type}] {decision.strip()}",
                            "DashboardInfluenced": influenced,
                            "Reason": reason.strip(),
                            "Outcome": "",
                            "Notes": notes.strip(),
                            "Ticker": SECTOR_ETFS[sector],
                        })
                        st.success("Saved.")
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")

            with st.expander("View past entries"):
                try:
                    j = read_journal()
                    if j.empty:
                        st.caption("No entries yet.")
                    else:
                        html_table(j.sort_values("Date", ascending=False).reset_index(drop=True))
                except Exception as e:
                    st.error(f"Couldn't load journal: {e}")

    with st.expander("Corporate action notes for this date"):
        notes_df = day.loc[day["CorporateActionNote"].notna(), ["Ticker", "CorporateActionNote"]]
        st.caption("None." if notes_df.empty else "")
        if not notes_df.empty:
            html_table(notes_df.reset_index(drop=True))


if __name__ == "__main__":
    main()
