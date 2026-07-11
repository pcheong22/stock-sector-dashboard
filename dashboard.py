"""
Minimal Streamlit dashboard reading from the storage layer.

Three views, per Phase 1 scope exactly:
    1. Global stock rankings
    2. Sector rankings
    3. Sector drill-down

No model comparison, no scorecard metrics, no decision journal UI here --
those are Phase 2/3 concerns and live elsewhere (decision_journal.py).
"""

from __future__ import annotations

import base64
import json
from datetime import date as date_cls

import pandas as pd
import requests
import streamlit as st

import storage
from momentum_model import aggregate_sector_breadth
from config import SECTOR_ETFS

st.set_page_config(page_title="Stock & Sector Rankings", layout="wide")

# --- GitHub-backed decision journal -----------------------------------
# Streamlit Cloud's filesystem is ephemeral and never syncs back to your
# repo, so logging a journal entry has to go through the GitHub API
# directly -- this writes the entry as a real commit to data/decision_journal.csv
# in your repo, the same file decision_journal.py / batch_review() reads.
#
# Requires three values in Streamlit Cloud's app secrets (Settings -> Secrets):
#   GITHUB_TOKEN = a fine-grained PAT with "Contents: Read and write" on this repo
#   GITHUB_REPO  = "yourusername/your-repo-name"
#   GITHUB_BRANCH = "main"   (optional, defaults to main)
JOURNAL_PATH_IN_REPO = "data/decision_journal.csv"
JOURNAL_COLUMNS = ["Date", "Decision", "DashboardInfluenced", "Reason", "Outcome", "Notes", "Ticker"]


def _gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def _gh_api_url():
    repo = st.secrets["GITHUB_REPO"]
    return f"https://api.github.com/repos/{repo}/contents/{JOURNAL_PATH_IN_REPO}"


def journal_configured() -> bool:
    return "GITHUB_TOKEN" in st.secrets and "GITHUB_REPO" in st.secrets


def append_journal_entry_via_github(entry: dict) -> None:
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    url = _gh_api_url()

    resp = requests.get(url, headers=_gh_headers(), params={"ref": branch})
    if resp.status_code == 200:
        payload = resp.json()
        existing_content = base64.b64decode(payload["content"]).decode("utf-8")
        sha = payload["sha"]
        from io import StringIO
        df = pd.read_csv(StringIO(existing_content))
    elif resp.status_code == 404:
        df = pd.DataFrame(columns=JOURNAL_COLUMNS)
        sha = None
    else:
        raise RuntimeError(f"GitHub API error reading journal: {resp.status_code} {resp.text}")

    df = pd.concat([df, pd.DataFrame([entry])], ignore_index=True)
    new_content = df.to_csv(index=False)
    encoded = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")

    body = {
        "message": f"Decision journal entry: {entry['Date']} ({entry.get('Ticker') or 'general'})",
        "content": encoded,
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    put_resp = requests.put(url, headers=_gh_headers(), data=json.dumps(body))
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub API error writing journal: {put_resp.status_code} {put_resp.text}")


def read_journal_via_github() -> pd.DataFrame:
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    resp = requests.get(_gh_api_url(), headers=_gh_headers(), params={"ref": branch})
    if resp.status_code == 404:
        return pd.DataFrame(columns=JOURNAL_COLUMNS)
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub API error reading journal: {resp.status_code} {resp.text}")
    content = base64.b64decode(resp.json()["content"]).decode("utf-8")
    from io import StringIO
    return pd.read_csv(StringIO(content))
# ------------------------------------------------------------------------


@st.cache_data(ttl=300)
def load_history():
    return storage.read_history()


def main():
    st.title("Daily Stock & Sector Ranking Dashboard")

    hist = load_history()
    if hist.empty:
        st.warning(
            "No data stored yet. Run `python run_daily.py` to fetch, score, "
            "and store the first day's data before using this dashboard."
        )
        return

    available_dates = sorted(hist["Date"].unique(), reverse=True)
    selected_date = st.sidebar.selectbox(
        "Date", available_dates, format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d")
    )
    day = hist[hist["Date"] == selected_date].copy()
    st.sidebar.caption(f"Model version: {day['ModelVersion'].iloc[0] if not day.empty else 'n/a'}")
    st.sidebar.caption(f"{len(day)} tickers scored on this date.")

    tab_sector, tab_global, tab_drill, tab_journal = st.tabs(
        ["Sector Rankings", "Global Rankings", "Sector Drill-Down", "Log a Decision"]
    )

    with tab_global:
        st.subheader("Global Stock Rankings")
        cols = ["Ticker", "Sector", "GlobalScore", "SectorScore", "Price",
                "ret_1M", "ret_3M", "ret_6M", "ret_12M", "rsi_14"]
        ranked = day.sort_values("GlobalScore", ascending=False)[cols].reset_index(drop=True)
        ranked.index += 1
        ranked.index.name = "Rank"
        st.dataframe(
            ranked.style.format({
                "GlobalScore": "{:.1f}", "SectorScore": "{:.1f}", "Price": "${:.2f}",
                "ret_1M": "{:.1%}", "ret_3M": "{:.1%}", "ret_6M": "{:.1%}", "ret_12M": "{:.1%}",
                "rsi_14": "{:.0f}",
            }),
            use_container_width=True,
            height=600,
        )

    with tab_sector:
        st.subheader("Sector Rankings")
        st.caption("Aggregated breadth: % of each sector's constituents scoring above 60.")

        sector_table = aggregate_sector_breadth(day)

        # Day-over-day change vs. the prior stored date, so a rotation signal
        # (e.g. one sector's breadth fading while another's builds) is visible
        # at a glance instead of requiring a manual compare across dates.
        idx = available_dates.index(selected_date)
        prior_date = available_dates[idx + 1] if idx + 1 < len(available_dates) else None

        if prior_date is not None:
            prior_day = hist[hist["Date"] == prior_date]
            prior_table = aggregate_sector_breadth(prior_day)[["Sector", "AvgGlobalScore", "BreadthPct"]]
            prior_table = prior_table.rename(columns={
                "AvgGlobalScore": "_PriorAvgGlobalScore", "BreadthPct": "_PriorBreadthPct",
            })
            sector_table = sector_table.merge(prior_table, on="Sector", how="left")
            sector_table["ScoreChange"] = sector_table["AvgGlobalScore"] - sector_table["_PriorAvgGlobalScore"]
            sector_table["BreadthChange"] = sector_table["BreadthPct"] - sector_table["_PriorBreadthPct"]
            sector_table = sector_table.drop(columns=["_PriorAvgGlobalScore", "_PriorBreadthPct"])
            st.caption(
                f"Changes are vs. {pd.Timestamp(prior_date).strftime('%Y-%m-%d')}, "
                f"the previous stored date."
            )
        else:
            sector_table["ScoreChange"] = pd.NA
            sector_table["BreadthChange"] = pd.NA
            st.caption("No prior date stored yet -- day-over-day change will appear once a second day is run.")

        sector_table = sector_table.reset_index(drop=True)
        sector_table.index += 1

        fmt = {
            "AvgGlobalScore": "{:.1f}", "MedianGlobalScore": "{:.1f}", "BreadthPct": "{:.0f}%",
            "ScoreChange": "{:+.1f}", "BreadthChange": "{:+.0f}pp",
        }
        def _color_change(val):
            if pd.isna(val):
                return ""
            if val > 2:
                return "background-color: #1a472a; color: white"
            if val > 0:
                return "background-color: #2d6a4f; color: white"
            if val < -2:
                return "background-color: #6b1a1a; color: white"
            if val < 0:
                return "background-color: #8b3a3a; color: white"
            return ""

        styled = sector_table.style.format(fmt, na_rep="—")
        if prior_date is not None:
            styled = styled.map(_color_change, subset=["ScoreChange", "BreadthChange"])
        st.dataframe(styled, use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.bar_chart(sector_table.set_index("Sector")["AvgGlobalScore"])
        with col2:
            if prior_date is not None:
                st.bar_chart(sector_table.set_index("Sector")["BreadthChange"])
                st.caption("Breadth change (percentage points) vs. prior date")

    with tab_drill:
        st.subheader("Sector Drill-Down")
        sectors = sorted(day["Sector"].unique())
        chosen = st.selectbox("Sector", sectors)
        sub = day[day["Sector"] == chosen].sort_values("SectorScore", ascending=False)
        cols = ["Ticker", "GlobalScore", "SectorScore", "Price",
                "ret_1M", "ret_3M", "ret_6M", "ret_12M",
                "dist_from_52w_high", "dist_from_52w_low", "realized_vol_21d",
                "trend_r2_63d", "rsi_14", "rel_ret_3M_vs_market", "rel_ret_3M_vs_sector"]
        sub_display = sub[cols].reset_index(drop=True)
        sub_display.index += 1
        st.dataframe(
            sub_display.style.format({
                "GlobalScore": "{:.1f}", "SectorScore": "{:.1f}", "Price": "${:.2f}",
                "ret_1M": "{:.1%}", "ret_3M": "{:.1%}", "ret_6M": "{:.1%}", "ret_12M": "{:.1%}",
                "dist_from_52w_high": "{:.1%}", "dist_from_52w_low": "{:.1%}",
                "realized_vol_21d": "{:.1%}", "trend_r2_63d": "{:.2f}", "rsi_14": "{:.0f}",
                "rel_ret_3M_vs_market": "{:.1%}", "rel_ret_3M_vs_sector": "{:.1%}",
            }),
            use_container_width=True,
            height=500,
        )

    with tab_journal:
        st.subheader("Log a Decision")
        st.caption(
            "Phase 2 daily log for sector-level relative-value calls (overweight/underweight "
            "between sectors, not individual stock picks). The Outcome field is intentionally "
            "not collected here -- it gets filled in during a Phase 3 batch review against "
            "forward price data, not logged by hand each day."
        )

        if not journal_configured():
            st.error(
                "Journal logging isn't configured yet. Add `GITHUB_TOKEN` and `GITHUB_REPO` "
                "(and optionally `GITHUB_BRANCH`) in this app's Settings → Secrets, then reload."
            )
        else:
            with st.form("journal_entry_form", clear_on_submit=True):
                entry_date = st.date_input("Date", value=date_cls.today())
                decision_type = st.selectbox(
                    "Decision type",
                    ["Open / increase overweight", "Trim / reduce overweight",
                     "Rotate into this sector from another", "Watch only (no action)"],
                )
                sector = st.selectbox("Sector", sorted(SECTOR_ETFS.keys()))
                decision = st.text_area(
                    "What did you decide? (e.g. 'Overweighted Technology vs. Energy in mock portfolio')",
                    height=80,
                )
                influenced = st.radio(
                    "Did the sector rankings/breadth change this vs. what you'd have done otherwise?",
                    ["Y", "N"], horizontal=True,
                )
                reason = st.text_area(
                    "Why? (cite the specific number -- AvgGlobalScore, BreadthPct, rank vs. other sectors)",
                    height=80,
                )
                notes = st.text_area("Anything else worth remembering? (optional)", height=60)
                submitted = st.form_submit_button("Save entry")

            if submitted:
                if not decision.strip():
                    st.warning("Add a description of the decision before saving.")
                else:
                    try:
                        append_journal_entry_via_github({
                            "Date": str(entry_date),
                            "Decision": f"[{decision_type}] {decision.strip()}",
                            "DashboardInfluenced": influenced,
                            "Reason": reason.strip(),
                            "Outcome": "",
                            "Notes": notes.strip(),
                            # Stored as the sector's ETF symbol so Phase 3's batch_review()
                            # can pull forward returns for it the same way it would a stock --
                            # the journal schema doesn't need to change, just what goes in Ticker.
                            "Ticker": SECTOR_ETFS[sector],
                        })
                        st.success("Saved to the decision journal.")
                    except Exception as e:
                        st.error(f"Couldn't save entry: {e}")

            with st.expander("View past entries"):
                try:
                    journal_df = read_journal_via_github()
                    if journal_df.empty:
                        st.caption("No entries logged yet.")
                    else:
                        st.dataframe(
                            journal_df.sort_values("Date", ascending=False),
                            use_container_width=True,
                        )
                except Exception as e:
                    st.error(f"Couldn't load journal: {e}")


        notes = day.loc[day["CorporateActionNote"].notna(), ["Ticker", "CorporateActionNote"]]
        if notes.empty:
            st.caption("None.")
        else:
            st.dataframe(notes, use_container_width=True)


if __name__ == "__main__":
    main()
