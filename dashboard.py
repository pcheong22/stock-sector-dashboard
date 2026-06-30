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

import pandas as pd
import streamlit as st

import storage
from momentum_model import aggregate_sector_breadth

st.set_page_config(page_title="Stock & Sector Rankings", layout="wide")


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

    tab_global, tab_sector, tab_drill = st.tabs(
        ["Global Rankings", "Sector Rankings", "Sector Drill-Down"]
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
        st.dataframe(
            sector_table.style.format({
                "AvgGlobalScore": "{:.1f}", "MedianGlobalScore": "{:.1f}", "BreadthPct": "{:.0f}%",
            }),
            use_container_width=True,
        )
        st.bar_chart(sector_table.set_index("Sector")["AvgGlobalScore"])

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

    with st.expander("Corporate action notes on file for this date"):
        notes = day.loc[day["CorporateActionNote"].notna(), ["Ticker", "CorporateActionNote"]]
        if notes.empty:
            st.caption("None.")
        else:
            st.dataframe(notes, use_container_width=True)


if __name__ == "__main__":
    main()
