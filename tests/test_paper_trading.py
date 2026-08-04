from pathlib import Path
import tempfile
import unittest

import pandas as pd

import paper_trading
from config import MARKET_BENCHMARK, SECTOR_ETFS


SECTORS = list(SECTOR_ETFS)


def scored_for(date: str) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Date": pd.Timestamp(date),
            "Ticker": f"TEST{index:02d}",
            "Sector": sector,
            "GlobalScore": 100 - index * 5,
            "ModelVersion": "test_v1",
        }
        for index, sector in enumerate(SECTORS)
    ])


def prices_for(date: str, overrides: dict[str, float] | None = None) -> pd.DataFrame:
    values = {symbol: 100.0 for symbol in set(SECTOR_ETFS.values()) | {MARKET_BENCHMARK}}
    values.update(overrides or {})
    return pd.DataFrame([
        {"Date": pd.Timestamp(date), "Ticker": symbol, "Close": close}
        for symbol, close in values.items()
    ])


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_initialization_creates_pending_signal_without_live_order(self):
        state = paper_trading.initialize_from_history(
            scored_for("2026-08-03"), self.data_dir
        )

        self.assertIsNotNone(state["pending_signal"])
        self.assertEqual(state["active_top"], [])
        self.assertEqual(state["top5_equity"], 1.0)
        self.assertFalse((self.data_dir / paper_trading.ORDERS_FILE).exists())
        signals = pd.read_csv(self.data_dir / paper_trading.SIGNALS_FILE)
        self.assertEqual(signals.loc[0, "Status"], "Pending")
        self.assertEqual(signals.loc[0, "EntryRule"], "Next trading-day close")

    def test_next_close_execution_costs_and_daily_mark_to_market(self):
        paper_trading.initialize_from_history(scored_for("2026-08-03"), self.data_dir)
        state = paper_trading.update(
            scored_for("2026-08-04"), prices_for("2026-08-04"), self.data_dir
        )

        self.assertAlmostEqual(state["top5_equity"], 0.999, places=12)
        self.assertAlmostEqual(state["long_short_equity"], 0.998, places=12)
        orders = pd.read_csv(self.data_dir / paper_trading.ORDERS_FILE)
        self.assertEqual(orders.loc[0, "LiveOrder"], "NO — PAPER ONLY")
        self.assertAlmostEqual(orders.loc[0, "TopTurnover"], 1.0)
        self.assertAlmostEqual(orders.loc[0, "BottomTurnover"], 1.0)

        top_symbols = [SECTOR_ETFS[sector] for sector in state["active_top"]]
        bottom_symbols = [SECTOR_ETFS[sector] for sector in state["active_bottom"]]
        overrides = {symbol: 101.0 for symbol in top_symbols}
        overrides.update({symbol: 99.0 for symbol in bottom_symbols})
        overrides[MARKET_BENCHMARK] = 100.5
        state = paper_trading.update(
            scored_for("2026-08-05"), prices_for("2026-08-05", overrides), self.data_dir
        )

        self.assertAlmostEqual(state["top5_equity"], 0.999 * 1.01, places=12)
        self.assertAlmostEqual(state["long_short_equity"], 0.998 * 1.02, places=12)
        self.assertAlmostEqual(state["spy_equity"], 1.005, places=12)

    def test_same_date_is_idempotent_and_signal_cadence_is_ten_days(self):
        paper_trading.initialize_from_history(scored_for("2026-08-03"), self.data_dir)
        state = None
        dates = pd.bdate_range("2026-08-04", periods=10)
        for date in dates:
            label = date.strftime("%Y-%m-%d")
            state = paper_trading.update(scored_for(label), prices_for(label), self.data_dir)

        self.assertIsNotNone(state["pending_signal"])
        self.assertEqual(state["pending_signal"]["signal_date"], dates[-1].strftime("%Y-%m-%d"))
        ledger_before = pd.read_csv(self.data_dir / paper_trading.LEDGER_FILE)
        same = paper_trading.update(
            scored_for(dates[-1].strftime("%Y-%m-%d")),
            prices_for(dates[-1].strftime("%Y-%m-%d")),
            self.data_dir,
        )
        ledger_after = pd.read_csv(self.data_dir / paper_trading.LEDGER_FILE)
        self.assertEqual(len(ledger_before), len(ledger_after))
        self.assertEqual(same, state)


if __name__ == "__main__":
    unittest.main()
